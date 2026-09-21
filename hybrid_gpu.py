#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import ctypes
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, TextIO, Tuple

PAUSE_SECONDS = 60.0
LOOP_INTERVAL_SEC = 5.0
MIN_RAM_AVAIL_BYTES = int(1.5 * 1024 ** 3)
MIN_RAM_AVAIL_RATIO = 0.08
MAX_ASSIST_BYTES = 768 * 1024 * 1024
MAX_ASSIST_RAM_RATIO = 0.12
MIN_LEFTOVER_AFTER_HYBRID = MIN_RAM_AVAIL_BYTES
COMMIT_PRESSURE_RATIO = 0.90
RAM_COLLAPSE_DROP_BYTES = 1024 ** 3
RAM_COLLAPSE_DROP_RATIO = 0.25
RAM_COLLAPSE_FLOOR_BYTES = 4 * 1024 ** 3
RAM_COLLAPSE_FLOOR_RATIO = 0.12
RAM_CLIMB_AVAIL_BYTES = int(2.5 * 1024 ** 3)
RAM_CLIMB_AVAIL_RATIO = 0.12
RAM_EASE_AVAIL_BYTES = int(2.0 * 1024 ** 3)
RAM_EASE_AVAIL_RATIO = 0.10
IDLE_RTX_UTIL = 15.0
IDLE_IGPU_UTIL = 20.0
TARGET_IGPU_UTIL = 28.0
TARGET_IGPU_MIN = 12.0
TARGET_IGPU_MAX = 38.0
TARGET_RTX_UTIL = 80.0
TARGET_RTX_MIN = 50.0
IGPU_HOG_UTIL = 42.0
IGPU_EMERGENCY_UTIL = 55.0
ASSIST_CLIMB_STEP = 2
ASSIST_DROP_STEP = 1
ASSIST_MIN_COPIES = 1
HOT_CPU_PCT = 8.0
HOT_GPU_PCT = 5.0
HOT_RTX_C = 87.0
HOT_RTX_ALONE_C = 92.0
HOT_IGPU_C = 80.0
HOT_IGPU_ALONE_C = 90.0
RTX_HIGH_UTIL = 70.0
RTX_STARVED_UTIL = 50.0
RTX_CLOCK_DROP_RATIO = 0.80
RTX_POWER_LIMIT_DROP_RATIO = 0.85
NVIDIA_CLOCK_FLOOR_RATIO = 0.95
NVIDIA_MEM_CLOCK_FLOOR_RATIO = 0.95
NVIDIA_UNLOCK_RETRY_SEC = 3.0
GAME_ON_NVIDIA_SM_PCT = 8.0
RTX_GAME_VRAM_MB = 400.0
MISPLACED_IGPU_UTIL = 50.0
MISPLACED_RTX_UTIL = 50.0
OFF_NVIDIA_COPIES = 2
OFF_NVIDIA_SLEEP_MS = 20
SUDDEN_UTIL_DROP_POINTS = 40.0
HISTORY_WINDOW = 12
TGP_HINT_W = 140.0
RELAUNCH_CONFIRM_CYCLES = 2
RELAUNCH_COOLDOWN_SEC = 20.0
LOCK_NAME = "hybrid_gpu.bae.lock"
WORKER_NAME = "hybrid-gpu-assist"
POLICY_REVISION = "2026.09.21.326"
ROBLOX_TARGET_FPS = 500
ROBLOX_CLIENT_FLAGS: Dict[str, Any] = {
    "DFIntTaskSchedulerTargetFps": ROBLOX_TARGET_FPS,
    "FFlagTaskSchedulerLimitTargetFpsTo240": False,
    "FFlagTaskSchedulerLimitTargetFpsTo2402": False,
    "FIntRobloxGuiMaxFramerate": ROBLOX_TARGET_FPS,
}
HIGH_PERF_POWER_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
ROBLOX_PIN_BASENAMES = (
    "RobloxPlayerBeta.exe",
    "RobloxPlayer.exe",
    "RobloxPlayerLauncher.exe",
    "RobloxStudioBeta.exe",
)
VALORANT_PIN_BASENAMES = (
    "VALORANT.exe",
    "VALORANT-Win64-Shipping.exe",
    "RiotClientServices.exe",
)
WEBOTS_PIN_BASENAMES = (
    "webots.exe",
    "webots-bin.exe",
    "coppeliaSim.exe",
)
GPU_HEAVY_PIN_BASENAMES = ROBLOX_PIN_BASENAMES + VALORANT_PIN_BASENAMES + WEBOTS_PIN_BASENAMES + (
    "Unity.exe",
    "UnrealEditor.exe",
    "UE4Editor.exe",
    "UE5Editor.exe",
    "UE4Game.exe",
    "UE5Game.exe",
    "GenshinImpact.exe",
    "StarRail.exe",
    "FortniteClient-Win64-Shipping.exe",
    "League of Legends.exe",
    "cs2.exe",
    "r5apex.exe",
)
GPU_HEAVY_NAME_TOKENS = (
    "roblox",
    "valorant",
    "webots",
    "cyberbotics",
    "coppelia",
    "gazebo",
    "isaac",
    "omniverse",
    "unreal",
    "unityplayer",
    "ue4game",
    "ue5game",
    "fortnite",
    "leagueoflegends",
    "genshin",
    "starrail",
    "eldenring",
    "cyberpunk",
)
RELAUNCH_SKIP_TOKENS = (
    "crashhandler",
    "crashpad",
    "easyanticheat",
    "battleye",
    "eac_",
    "vanguard",
    "vgtray",
    "vgc.exe",
)
IGPU_REMAINDER_BASENAMES = (
    "chrome.exe",
    "msedge.exe",
    "msedgewebview2.exe",
    "firefox.exe",
    "discord.exe",
    "spotify.exe",
    "telegram.exe",
    "slack.exe",
    "code.exe",
    "cursor.exe",
)

AMD_VENDOR_ID = 0x1002
NVIDIA_VENDOR_ID = 0x10DE
INTEL_VENDOR_ID = 0x8086
APPLE_VENDOR_ID = 0x106B
QUALCOMM_VENDOR_ID = 0x17CB
QUALCOMM_LEGACY_ID = 0x5143
ARM_VENDOR_ID = 0x13B5
IMG_VENDOR_ID = 0x1010
BROADCOM_VENDOR_ID = 0x14E4
MOORETHREADS_VENDOR_ID = 0x1ED5
SAMSUNG_VENDOR_ID = 0x144D
HUAWEI_VENDOR_ID = 0x19E5
VENDOR_IDS = {
    "nvidia": NVIDIA_VENDOR_ID,
    "amd": AMD_VENDOR_ID,
    "intel": INTEL_VENDOR_ID,
    "apple": APPLE_VENDOR_ID,
    "qualcomm": QUALCOMM_VENDOR_ID,
    "arm": ARM_VENDOR_ID,
    "img": IMG_VENDOR_ID,
    "broadcom": BROADCOM_VENDOR_ID,
    "moorethreads": MOORETHREADS_VENDOR_ID,
    "samsung": SAMSUNG_VENDOR_ID,
    "huawei": HUAWEI_VENDOR_ID,
}
INTEGRATED_HINTS = (
    "uhd graphics",
    "iris",
    "hd graphics",
    "gma 950",
    "gma 3150",
    "gma 4500",
    "poulsbo",
    "processor graphics",
    "core ultra",
    "radeon graphics",
    "780m",
    "760m",
    "740m",
    "680m",
    "660m",
    "610m",
    "890m",
    "880m",
    "860m",
    "radeon 780",
    "radeon 760",
    "radeon 890",
    "vega 8",
    "vega 7",
    "quick sync",
    "arc graphics",
    "apple m1",
    "apple m2",
    "apple m3",
    "apple m4",
    "apple m5",
    "apple gpu",
    "asahi",
    "apple-agx",
    "adreno",
    "mali-",
    "mali g",
    "powervr",
    "imagination",
    "pvrsrvkm",
    "snapdragon",
    "arc 140v",
    "arc 140t",
    "arc 130v",
    "arc 130t",
    "arc 110v",
    "arc 110t",
    "intel graphics",
    "meteor lake",
    "lunar lake",
    "arrow lake",
    "8060s",
    "8050s",
    "8040s",
    "strix halo",
    "strix point",
    "hawk point",
    "phoenix",
    "x1-85",
    "x1-45",
    "x1-54",
    "x1-44",
    "x1-26",
    "x1-55",
    "x1-35",
    "x1-32",
    "x1-28",
    "x1-42",
    "x1-24",
    "x1-22",
    "x1-20",
    "x1-18",
    "x1-16",
    "x1-14",
    "x1-12",
    "x1-10",
    "x1-08",
    "x1-06",
    "x1-04",
    "x1-02",
    "x1-01",
    "x1-09",
    "x1-21",
    "x1-30",
    "x1-31",
    "x1-33",
    "x1-34",
    "x1-36",
    "x1-37",
    "x1-38",
    "x1-39",
    "x1-40",
    "x1-41",
    "x1-43",
    "x1-48",
    "x1-49",
    "x1-50",
    "x1-51",
    "x1-52",
    "x1-53",
    "x1-57",
    "x1-58",
    "x1-59",
    "x1-60",
    "x1-61",
    "x1-62",
    "x1-63",
    "x1-64",
    "x1-65",
    "x1-66",
    "x1-67",
    "x1-68",
    "x1-69",
    "x1-70",
    "x1-71",
    "x1-72",
    "x1-73",
    "x1-74",
    "x1-75",
    "x1-76",
    "x1-77",
    "x1-78",
    "x1-79",
    "x1-80",
    "x1-81",
    "x1-82",
    "x1-83",
    "x1-84",
    "x1-86",
    "x1-87",
    "x1-88",
    "x1-89",
    "x1-90",
    "x1-91",
    "x1-92",
    "x1-93",
    "x1-94",
    "x1-95",
    "x1-96",
    "x1-97",
    "x1-98",
    "x1-99",
    "x1-100",
    "x1-101",
    "x1-102",
    "x1-103",
    "x1-104",
    "x1-105",
    "x1-106",
    "x1-107",
    "x1-108",
    "x1-109",
    "x1-110",
    "x1-111",
    "x1-112",
    "x1-113",
    "x1-114",
    "x1-115",
    "x1-116",
    "x1-117",
    "x1-118",
    "x1-119",
    "x1-120",
    "x1-121",
    "x1-122",
    "x1-123",
    "x1-124",
    "x1-125",
    "x elite",
    "x plus",
    "x1p",
    "840m",
    "830m",
    "krackan",
    "ryzen ai",
    "videocore",
    "v3d",
    "bcm271",
    "vc4",
    "immortalis",
    "xclipse",
    "maleoon",
    "vivante",
    "verisilicon",
    "etnaviv",
    "panfrost",
    "panthor",
    "lima",
)
DISCRETE_HINTS = (
    "geforce",
    "iris xe max",
    "dg1",
    "dg2",
    "rtx",
    "gtx",
    "quadro",
    "tesla",
    "nvidia t1",
    "nvidia t4",
    "nvidia t6",
    "nvidia l4",
    "nvidia l40",
    "nvidia a10",
    "nvidia a16",
    "nvidia a40",
    "tesla v100",
    "tesla p100",
    "tesla k80",
    "titan",
    "nvidia mx",
    "mx150",
    "mx250",
    "mx330",
    "mx350",
    "mx450",
    "mx550",
    "mx570",
    "mtt s",
    "moore threads",
    "radeon rx",
    "rx 7",
    "rx 6",
    "rx 8",
    "rx 9",
    "rx 5",
    "arc a",
    "arc b7",
    "arc b5",
    "arc b58",
    "arc b6",
    "arc pro",
    "radeon pro",
    "firepro",
    "instinct",
    "radeon vii",
)
SKIP_ADAPTER_HINTS = (
    "microsoft basic",
    "remote display",
    "virtual gpu",
    "citrix",
    "parsec",
    "llvmpipe",
    "softpipe",
    "swiftshader",
    "virtualbox",
    "hyper-v",
    "rdpdd",
    "vnc",
    "standard vga",
    "basic display",
    "virtio",
    "qxl",
    "cirrus",
    "vmware svga",
    "vboxvga",
    "bochs",
    "aspeed",
    "matrox g200",
    "gdi generic",
    "microsoft kernel",
    "frame buffer",
    "efifb",
    "simpledrm",
    "vesa",
    "xen",
    "dummy",
    "vgem",
    "vkms",
    "displaylink",
    "evdi",
)

GPU_NAME_CATALOG: Tuple[Tuple[str, str, bool], ...] = (
    ("NVIDIA GeForce RTX 5060 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce MX550", "nvidia", False),
    ("NVIDIA GeForce MX350", "nvidia", False),
    ("NVIDIA GeForce RTX 4070", "nvidia", False),
    ("NVIDIA GeForce RTX 4070 Ti", "nvidia", False),
    ("NVIDIA GeForce RTX 4070 SUPER", "nvidia", False),
    ("NVIDIA GeForce RTX 4070 Ti SUPER", "nvidia", False),
    ("NVIDIA GeForce RTX 4070 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 3080 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 3070", "nvidia", False),
    ("NVIDIA GeForce RTX 3060", "nvidia", False),
    ("NVIDIA GeForce RTX 3050", "nvidia", False),
    ("NVIDIA GeForce RTX 4090 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 4080", "nvidia", False),
    ("NVIDIA GeForce RTX 4050 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 4060", "nvidia", False),
    ("NVIDIA GeForce RTX 2060", "nvidia", False),
    ("NVIDIA GeForce RTX 2080 Super", "nvidia", False),
    ("NVIDIA GeForce GTX 1650", "nvidia", False),
    ("NVIDIA GeForce GTX 1660 Ti", "nvidia", False),
    ("NVIDIA GeForce GTX 1080", "nvidia", False),
    ("NVIDIA GeForce GTX 1050 Ti", "nvidia", False),
    ("NVIDIA T1200 Laptop GPU", "nvidia", False),
    ("NVIDIA Tesla T4", "nvidia", False),
    ("NVIDIA L4", "nvidia", False),
    ("NVIDIA A10", "nvidia", False),
    ("NVIDIA A40", "nvidia", False),
    ("NVIDIA GeForce RTX 5050 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 4060 Laptop GPU", "nvidia", False),
    ("NVIDIA RTX A2000 Laptop GPU", "nvidia", False),
    ("NVIDIA Quadro P2000", "nvidia", False),
    ("NVIDIA Quadro RTX 4000", "nvidia", False),
    ("AMD FirePro W7170M", "amd", False),
    ("AMD FirePro W5170M", "amd", False),
    ("AMD Radeon VII", "amd", False),
    ("NVIDIA RTX 2000 Ada Generation Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 580", "amd", False),
    ("AMD Radeon RX 5500 XT", "amd", False),
    ("AMD Radeon RX 5700 XT", "amd", False),
    ("AMD Radeon RX 6700 XT", "amd", False),
    ("AMD Radeon RX 6600", "amd", False),
    ("AMD Radeon RX 6500 XT", "amd", False),
    ("AMD Radeon RX 6400", "amd", False),
    ("AMD Radeon RX 7800 XT", "amd", False),
    ("AMD Radeon RX 7700 XT", "amd", False),
    ("AMD Radeon RX 7900 XTX", "amd", False),
    ("AMD Radeon RX 7600M XT", "amd", False),
    ("AMD Radeon RX 7600", "amd", False),
    ("AMD Radeon RX 6800M", "amd", False),
    ("AMD Radeon RX 6800", "amd", False),
    ("AMD Radeon RX 6900 XT", "amd", False),
    ("AMD Radeon 780M Graphics", "amd", True),
    ("AMD Radeon 760M Graphics", "amd", True),
    ("AMD Radeon 740M Graphics", "amd", True),
    ("AMD Radeon 610M Graphics", "amd", True),
    ("AMD Radeon 660M Graphics", "amd", True),
    ("AMD Radeon 680M Graphics", "amd", True),
    ("AMD Radeon 890M Graphics", "amd", True),
    ("AMD Radeon 880M Graphics", "amd", True),
    ("AMD Radeon 860M Graphics", "amd", True),
    ("AMD Radeon Graphics", "amd", True),
    ("AMD Radeon Graphics (Phoenix)", "amd", True),
    ("AMD Radeon Vega 8 Graphics", "amd", True),
    ("AMD Radeon Vega 7 Graphics", "amd", True),
    ("Intel UHD Graphics", "intel", True),
    ("Intel UHD Graphics 770", "intel", True),
    ("Intel UHD Graphics 730", "intel", True),
    ("Intel HD Graphics 620", "intel", True),
    ("Intel HD Graphics 520", "intel", True),
    ("Intel UHD Graphics 630", "intel", True),
    ("Intel UHD Graphics 617", "intel", True),
    ("Intel Iris Xe Graphics", "intel", True),
    ("Intel Iris Xe Graphics G7", "intel", True),
    ("Intel Iris Plus Graphics", "intel", True),
    ("Intel Iris Xe MAX Graphics", "intel", False),
    ("Intel Arc Graphics 140V", "intel", True),
    ("AMD Radeon RX 8800 XT", "amd", False),
    ("AMD Radeon RX 9060 XT", "amd", False),
    ("AMD Radeon RX 9070 XT", "amd", False),
    ("Intel Arc A770", "intel", False),
    ("Intel Arc A770M", "intel", False),
    ("Intel Arc A750", "intel", False),
    ("Intel Arc A380", "intel", False),
    ("Intel Arc A580", "intel", False),
    ("Intel Arc B580", "intel", False),
    ("Intel Arc B570", "intel", False),
    ("Intel Arc Pro A60", "intel", False),
    ("Apple M1 GPU", "apple", True),
    ("Apple M1 Pro GPU", "apple", True),
    ("Apple M2 GPU", "apple", True),
    ("Apple M2 Pro GPU", "apple", True),
    ("Apple M2 Max GPU", "apple", True),
    ("Apple M3 GPU", "apple", True),
    ("Apple M3 Pro GPU", "apple", True),
    ("Apple M4 GPU", "apple", True),
    ("Apple M4 Pro GPU", "apple", True),
    ("AMD Radeon 8050S Graphics", "amd", True),
    ("AMD Radeon 8060S Graphics", "amd", True),
    ("Intel Arc Graphics 130V", "intel", True),
    ("Qualcomm Snapdragon X Plus Adreno", "qualcomm", True),
    ("Qualcomm Adreno X1-85", "qualcomm", True),
    ("NVIDIA GeForce RTX 5070 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 5080 Laptop GPU", "nvidia", False),
    ("NVIDIA GeForce RTX 5090 Laptop GPU", "nvidia", False),
    ("Intel Graphics", "intel", True),
    ("Intel Arc Graphics for Core Ultra", "intel", True),
    ("Qualcomm Adreno 740", "qualcomm", True),
    ("Qualcomm Adreno 730", "qualcomm", True),
    ("Qualcomm Adreno 680", "qualcomm", True),
    ("Qualcomm Adreno 650", "qualcomm", True),
    ("ARM Mali-G710", "arm", True),
    ("ARM Mali-G78", "arm", True),
    ("ARM Mali-G52", "arm", True),
    ("ARM Mali-G610", "arm", True),
    ("ARM Mali-G57", "arm", True),
    ("PowerVR B-Series BXE-4-32", "img", True),
    ("Imagination PowerVR GE8320", "img", True),
    ("PowerVR Rogue G6430", "img", True),
    ("AMD Radeon 840M Graphics", "amd", True),
    ("AMD Radeon Graphics (Krackan Point)", "amd", True),
    ("Intel Arc B50", "intel", False),
    ("Apple M4 Max GPU", "apple", True),
    ("Qualcomm Adreno X1-45", "qualcomm", True),
    ("NVIDIA GeForce RTX 4090", "nvidia", False),
    ("AMD Radeon RX 7900 XT", "amd", False),
    ("Intel Arc A350M", "intel", False),
    ("ARM Mali-G720", "arm", True),
    ("NVIDIA RTX 4000 SFF Ada Generation", "nvidia", False),
    ("AMD Radeon 830M Graphics", "amd", True),
    ("NVIDIA GeForce RTX 3080", "nvidia", False),
    ("NVIDIA GeForce GTX 1070", "nvidia", False),
    ("AMD Radeon RX 570", "amd", False),
    ("Intel Arc Pro B60", "intel", False),
    ("Apple M3 Max GPU", "apple", True),
    ("Qualcomm Adreno 642L", "qualcomm", True),
    ("NVIDIA GeForce RTX 2070 Super", "nvidia", False),
    ("NVIDIA GeForce MX570", "nvidia", False),
    ("AMD Radeon RX 550", "amd", False),
    ("Intel HD Graphics 4000", "intel", True),
    ("ARM Mali-G68", "arm", True),
    ("Apple M1 Max GPU", "apple", True),
    ("NVIDIA GeForce RTX 3060 Ti", "nvidia", False),
    ("AMD Radeon RX 6650 XT", "amd", False),
    ("Intel Arc A530M", "intel", False),
    ("ARM Mali-G510", "arm", True),
    ("Imagination PowerVR BXM-8-256", "img", True),
    ("Qualcomm Adreno 619", "qualcomm", True),
    ("Broadcom VideoCore VII", "broadcom", True),
    ("Broadcom V3D", "broadcom", True),
    ("NVIDIA GeForce RTX 2080 Ti", "nvidia", False),
    ("AMD Radeon RX 5600 XT", "amd", False),
    ("Intel UHD Graphics 620", "intel", True),
    ("ARM Mali-G31", "arm", True),
    ("NVIDIA GeForce RTX 3080 Ti", "nvidia", False),
    ("AMD Radeon RX 6750 XT", "amd", False),
    ("Intel Arc A370M", "intel", False),
    ("ARM Mali-G76", "arm", True),
    ("Apple M2 Ultra GPU", "apple", True),
    ("Qualcomm Adreno 725", "qualcomm", True),
    ("NVIDIA GeForce GTX 980 Ti", "nvidia", False),
    ("AMD Radeon RX 480", "amd", False),
    ("Intel Iris Plus Graphics 655", "intel", True),
    ("ARM Mali-G77", "arm", True),
    ("Apple M1 Ultra GPU", "apple", True),
    ("Qualcomm Adreno 540", "qualcomm", True),
    ("NVIDIA GeForce RTX 2060 Super", "nvidia", False),
    ("AMD Radeon RX 6950 XT", "amd", False),
    ("Intel Arc Pro A40", "intel", False),
    ("ARM Immortalis-G720", "arm", True),
    ("Apple M3 Ultra GPU", "apple", True),
    ("Qualcomm Adreno 643", "qualcomm", True),
    ("NVIDIA GeForce RTX 3070 Ti", "nvidia", False),
    ("AMD Radeon RX 7700", "amd", False),
    ("Intel HD Graphics 5500", "intel", True),
    ("ARM Mali-T860", "arm", True),
    ("Apple M4 Ultra GPU", "apple", True),
    ("Qualcomm Adreno 735", "qualcomm", True),
    ("NVIDIA TITAN RTX", "nvidia", False),
    ("AMD Radeon RX 6800 XT", "amd", False),
    ("Intel UHD Graphics 750", "intel", True),
    ("ARM Mali-G51", "arm", True),
    ("NVIDIA GeForce GTX 970", "nvidia", False),
    ("Qualcomm Adreno 660", "qualcomm", True),
    ("NVIDIA GeForce RTX 3080 Ti Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 7600 XT", "amd", False),
    ("Intel Iris Xe Graphics G4", "intel", True),
    ("ARM Mali-G72", "arm", True),
    ("Imagination PowerVR GT7600", "img", True),
    ("Qualcomm Adreno 620", "qualcomm", True),
    ("NVIDIA GeForce RTX 4080 SUPER", "nvidia", False),
    ("AMD Radeon RX 7900 GRE", "amd", False),
    ("Intel Arc A310", "intel", False),
    ("ARM Mali-G71", "arm", True),
    ("NVIDIA Quadro T2000", "nvidia", False),
    ("Qualcomm Adreno 508", "qualcomm", True),
    ("NVIDIA GeForce RTX 4050", "nvidia", False),
    ("AMD Radeon RX 6500M", "amd", False),
    ("Intel UHD Graphics 610", "intel", True),
    ("ARM Mali-G310", "arm", True),
    ("NVIDIA RTX A4000", "nvidia", False),
    ("Qualcomm Adreno 506", "qualcomm", True),
    ("NVIDIA GeForce GTX 1630", "nvidia", False),
    ("AMD Radeon RX 5500M", "amd", False),
    ("Intel HD Graphics 530", "intel", True),
    ("ARM Mali-T880", "arm", True),
    ("NVIDIA RTX A5000", "nvidia", False),
    ("Qualcomm Adreno 504", "qualcomm", True),
    ("NVIDIA GeForce RTX 4090 D", "nvidia", False),
    ("AMD Radeon RX 7900", "amd", False),
    ("Intel Arc A60M", "intel", False),
    ("ARM Mali-G78AE", "arm", True),
    ("NVIDIA GeForce GT 1030", "nvidia", False),
    ("Qualcomm Adreno 430", "qualcomm", True),
    ("NVIDIA GeForce RTX 3050 Ti Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 6600M", "amd", False),
    ("Intel UHD Graphics 605", "intel", True),
    ("ARM Immortalis-G715", "arm", True),
    ("NVIDIA Quadro P1000", "nvidia", False),
    ("Qualcomm Adreno 418", "qualcomm", True),
    ("NVIDIA GeForce RTX 4060 Ti", "nvidia", False),
    ("AMD Radeon RX 6700", "amd", False),
    ("Intel HD Graphics 4600", "intel", True),
    ("ARM Mali-G715", "arm", True),
    ("NVIDIA RTX A6000", "nvidia", False),
    ("Qualcomm Adreno 330", "qualcomm", True),
    ("Moore Threads MTT S80", "moorethreads", False),
    ("Moore Threads MTT S70", "moorethreads", False),
    ("NVIDIA GeForce RTX 2080", "nvidia", False),
    ("AMD Radeon RX 5700", "amd", False),
    ("Intel UHD Graphics 600", "intel", True),
    ("ARM Mali-G52 MP2", "arm", True),
    ("NVIDIA GeForce RTX 4080 Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 7600M", "amd", False),
    ("Intel UHD Graphics 615", "intel", True),
    ("ARM Mali-G57 MP2", "arm", True),
    ("NVIDIA RTX 5000 Ada Generation", "nvidia", False),
    ("Qualcomm Adreno 308", "qualcomm", True),
    ("NVIDIA GeForce GTX 1060", "nvidia", False),
    ("AMD Radeon RX 5500", "amd", False),
    ("Intel HD Graphics 4400", "intel", True),
    ("ARM Mali-G51 MP4", "arm", True),
    ("NVIDIA Quadro RTX 5000", "nvidia", False),
    ("Qualcomm Adreno 220", "qualcomm", True),
    ("NVIDIA GeForce RTX 3070 Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 6700M", "amd", False),
    ("Intel HD Graphics 3000", "intel", True),
    ("ARM Mali-G78 MP24", "arm", True),
    ("NVIDIA RTX A4500", "nvidia", False),
    ("Qualcomm Adreno 205", "qualcomm", True),
    ("NVIDIA GeForce GTX 980", "nvidia", False),
    ("AMD Radeon RX 7800", "amd", False),
    ("Intel HD Graphics 510", "intel", True),
    ("ARM Mali-G76 MP16", "arm", True),
    ("NVIDIA RTX A2000", "nvidia", False),
    ("Qualcomm Adreno 200", "qualcomm", True),
    ("NVIDIA GeForce RTX 3060 Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 6600 XT", "amd", False),
    ("Intel Iris Graphics 540", "intel", True),
    ("ARM Mali-G57 MP5", "arm", True),
    ("NVIDIA GeForce RTX 5080", "nvidia", False),
    ("Qualcomm Adreno 320", "qualcomm", True),
    ("NVIDIA GeForce RTX 4070 SUPER Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 7650 GRE", "amd", False),
    ("Intel Iris Plus Graphics 640", "intel", True),
    ("ARM Mali-G52 MP6", "arm", True),
    ("NVIDIA GeForce RTX 5090", "nvidia", False),
    ("Qualcomm Adreno 305", "qualcomm", True),
    ("NVIDIA GeForce GTX 750 Ti", "nvidia", False),
    ("AMD Radeon RX 6300", "amd", False),
    ("Intel HD Graphics 2500", "intel", True),
    ("ARM Mali-G57 MP1", "arm", True),
    ("NVIDIA Quadro T1000", "nvidia", False),
    ("Qualcomm Adreno 304", "qualcomm", True),
    ("NVIDIA GeForce RTX 2060 Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 6650M", "amd", False),
    ("Intel HD Graphics 500", "intel", True),
    ("ARM Mali-G78 MP14", "arm", True),
    ("NVIDIA T600", "nvidia", False),
    ("Qualcomm Adreno 203", "qualcomm", True),
    ("NVIDIA GeForce GTX 960", "nvidia", False),
    ("AMD Radeon RX 560", "amd", False),
    ("Intel HD Graphics 2000", "intel", True),
    ("ARM Mali-G51 MP8", "arm", True),
    ("NVIDIA RTX A1000", "nvidia", False),
    ("Qualcomm Adreno 302", "qualcomm", True),
    ("NVIDIA GeForce GTX 1050", "nvidia", False),
    ("AMD Radeon RX 590", "amd", False),
    ("Intel HD Graphics 4200", "intel", True),
    ("ARM Mali-G52 MP1", "arm", True),
    ("NVIDIA RTX A3000 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 301", "qualcomm", True),
    ("NVIDIA GeForce GTX 950", "nvidia", False),
    ("AMD Radeon RX 470", "amd", False),
    ("Intel HD Graphics 400", "intel", True),
    ("ARM Mali-G31 MP2", "arm", True),
    ("NVIDIA Quadro P620", "nvidia", False),
    ("Qualcomm Adreno 306", "qualcomm", True),
    ("NVIDIA GeForce GTX 1650 SUPER", "nvidia", False),
    ("AMD Radeon RX 5600", "amd", False),
    ("Intel HD Graphics 610", "intel", True),
    ("ARM Mali-G76 MP4", "arm", True),
    ("NVIDIA RTX A5500 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 318", "qualcomm", True),
    ("NVIDIA GeForce RTX 4060 SUPER", "nvidia", False),
    ("AMD Radeon RX 7600S", "amd", False),
    ("Intel Iris Xe Graphics 96EU", "intel", True),
    ("ARM Mali-G52 MP4", "arm", True),
    ("NVIDIA RTX 3500 Ada Generation Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 420", "qualcomm", True),
    ("NVIDIA GeForce GTX 1660 SUPER", "nvidia", False),
    ("AMD Radeon RX 6800S", "amd", False),
    ("Intel UHD Graphics 24EU", "intel", True),
    ("ARM Mali-G610 MP6", "arm", True),
    ("NVIDIA RTX A4000 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 512", "qualcomm", True),
    ("NVIDIA GeForce RTX 4070 Ti Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 7900M", "amd", False),
    ("Intel UHD Graphics 64EU", "intel", True),
    ("ARM Mali-G78 MP20", "arm", True),
    ("NVIDIA RTX 3000 Ada Generation Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 530", "qualcomm", True),
    ("NVIDIA GeForce RTX 4080 SUPER Laptop GPU", "nvidia", False),
    ("AMD Radeon RX 7700S", "amd", False),
    ("Intel Arc Graphics 110V", "intel", True),
    ("ARM Mali-G310 MP2", "arm", True),
    ("NVIDIA GeForce RTX 5070", "nvidia", False),
    ("Qualcomm Adreno 613", "qualcomm", True),
    ("NVIDIA GeForce GTX 1070 Ti", "nvidia", False),
    ("AMD Radeon RX 6850M XT", "amd", False),
    ("Intel HD Graphics 5000", "intel", True),
    ("ARM Mali-G57 MP4", "arm", True),
    ("NVIDIA RTX 4000 Ada Generation", "nvidia", False),
    ("Qualcomm Adreno 509", "qualcomm", True),
    ("NVIDIA GeForce GTX 1080 Ti", "nvidia", False),
    ("AMD Radeon RX 5700M", "amd", False),
    ("Intel UHD Graphics 710", "intel", True),
    ("ARM Mali-G76 MP8", "arm", True),
    ("NVIDIA GeForce RTX 5050", "nvidia", False),
    ("Qualcomm Adreno 616", "qualcomm", True),
    ("NVIDIA GeForce MX450", "nvidia", False),
    ("AMD Radeon RX 6500", "amd", False),
    ("Intel HD Graphics 630", "intel", True),
    ("ARM Mali-G68 MP5", "arm", True),
    ("NVIDIA RTX A500 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 642", "qualcomm", True),
    ("NVIDIA GeForce GTX 780 Ti", "nvidia", False),
    ("AMD Radeon RX 580 2048SP", "amd", False),
    ("Intel Iris Plus Graphics 645", "intel", True),
    ("ARM Mali-G52 MP8", "arm", True),
    ("NVIDIA Quadro RTX 3000", "nvidia", False),
    ("Qualcomm Adreno 618", "qualcomm", True),
    ("NVIDIA GeForce GTX 770", "nvidia", False),
    ("AMD Radeon RX 460", "amd", False),
    ("Intel HD Graphics 5600", "intel", True),
    ("ARM Mali-G57 MP3", "arm", True),
    ("NVIDIA RTX A2000 12GB", "nvidia", False),
    ("Qualcomm Adreno 640", "qualcomm", True),
    ("NVIDIA GeForce GTX 760", "nvidia", False),
    ("AMD Radeon RX 550X", "amd", False),
    ("Intel HD Graphics 505", "intel", True),
    ("ARM Mali-G51 MP2", "arm", True),
    ("NVIDIA Quadro P4000", "nvidia", False),
    ("Qualcomm Adreno 630", "qualcomm", True),
    ("NVIDIA GeForce GTX 750", "nvidia", False),
    ("AMD Radeon RX 5300M", "amd", False),
    ("Intel HD Graphics 515", "intel", True),
    ("ARM Mali-G52 MP3", "arm", True),
    ("NVIDIA Quadro RTX 6000", "nvidia", False),
    ("Qualcomm Adreno 710", "qualcomm", True),
    ("NVIDIA GeForce GTX 660", "nvidia", False),
    ("AMD Radeon RX 540", "amd", False),
    ("Intel HD Graphics 6000", "intel", True),
    ("ARM Mali-G76 MP2", "arm", True),
    ("NVIDIA RTX A1000 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 644", "qualcomm", True),
    ("NVIDIA GeForce GTX 650", "nvidia", False),
    ("AMD Radeon RX 560X", "amd", False),
    ("Intel HD Graphics 5300", "intel", True),
    ("ARM Mali-G51 MP6", "arm", True),
    ("NVIDIA Quadro T600", "nvidia", False),
    ("Qualcomm Adreno 608", "qualcomm", True),
    ("NVIDIA GeForce GTX 980M", "nvidia", False),
    ("AMD Radeon RX 5600M", "amd", False),
    ("Intel UHD Graphics 618", "intel", True),
    ("ARM Mali-G72 MP3", "arm", True),
    ("NVIDIA RTX A4500 Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 605", "qualcomm", True),
    ("NVIDIA GeForce GTX 860M", "nvidia", False),
    ("AMD Radeon RX 5300", "amd", False),
    ("Intel HD Graphics 615", "intel", True),
    ("ARM Mali-G57 MP6", "arm", True),
    ("NVIDIA RTX A3000 12GB Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 612", "qualcomm", True),
    ("NVIDIA GeForce GTX 870M", "nvidia", False),
    ("AMD Radeon RX 540X", "amd", False),
    ("Intel HD Graphics 550", "intel", True),
    ("ARM Mali-G68 MP2", "arm", True),
    ("NVIDIA RTX A5500", "nvidia", False),
    ("Qualcomm Adreno 621", "qualcomm", True),
    ("NVIDIA GeForce GTX 880M", "nvidia", False),
    ("AMD Radeon RX 6450M", "amd", False),
    ("Intel HD Graphics 535", "intel", True),
    ("ARM Mali-G76 MP12", "arm", True),
    ("NVIDIA TITAN V", "nvidia", False),
    ("Qualcomm Adreno 675", "qualcomm", True),
    ("NVIDIA GeForce GTX 780", "nvidia", False),
    ("AMD Radeon RX 6700S", "amd", False),
    ("Intel HD Graphics 440", "intel", True),
    ("ARM Mali-G78 MP10", "arm", True),
    ("NVIDIA TITAN Xp", "nvidia", False),
    ("Qualcomm Adreno 685", "qualcomm", True),
    ("NVIDIA GeForce GTX 970M", "nvidia", False),
    ("AMD Radeon RX 7700M", "amd", False),
    ("Intel HD Graphics 5100", "intel", True),
    ("ARM Mali-G52 MP10", "arm", True),
    ("NVIDIA TITAN X", "nvidia", False),
    ("Qualcomm Adreno 690", "qualcomm", True),
    ("NVIDIA GeForce GTX 960M", "nvidia", False),
    ("AMD Radeon RX 6550M", "amd", False),
    ("Intel HD Graphics 5200", "intel", True),
    ("ARM Mali-G68 MP4", "arm", True),
    ("NVIDIA GeForce GTX 1050M", "nvidia", False),
    ("Qualcomm Adreno 830", "qualcomm", True),
    ("NVIDIA GeForce GTX 950M", "nvidia", False),
    ("AMD Radeon RX 6600S", "amd", False),
    ("Intel HD Graphics 6100", "intel", True),
    ("ARM Mali-G51 MP3", "arm", True),
    ("NVIDIA GeForce GTX 1650M", "nvidia", False),
    ("Qualcomm Adreno 732", "qualcomm", True),
    ("NVIDIA GeForce GTX 850M", "nvidia", False),
    ("AMD Radeon RX 6550S", "amd", False),
    ("Intel Iris Graphics 550", "intel", True),
    ("ARM Mali-G72 MP12", "arm", True),
    ("NVIDIA GeForce GTX 1060M", "nvidia", False),
    ("Qualcomm Adreno 750", "qualcomm", True),
    ("NVIDIA GeForce GTX 1070M", "nvidia", False),
    ("AMD Radeon RX 7800M", "amd", False),
    ("Intel HD Graphics 6200", "intel", True),
    ("ARM Mali-G78 MP22", "arm", True),
    ("NVIDIA GeForce RTX 3080 12GB", "nvidia", False),
    ("Qualcomm Adreno 820", "qualcomm", True),
    ("NVIDIA GeForce GTX 1080M", "nvidia", False),
    ("AMD Radeon RX 6650S", "amd", False),
    ("Intel HD Graphics 6300", "intel", True),
    ("ARM Mali-G57 MP8", "arm", True),
    ("NVIDIA GeForce RTX 3070 Ti Laptop GPU", "nvidia", False),
    ("Qualcomm Adreno 840", "qualcomm", True),
    ("NVIDIA GeForce GTX 765M", "nvidia", False),
    ("AMD Radeon RX 6500S", "amd", False),
    ("Intel Iris Graphics 5100", "intel", True),
    ("ARM Mali-G52 MP12", "arm", True),
    ("NVIDIA GeForce RTX 3060 12GB", "nvidia", False),
    ("Qualcomm Adreno 663", "qualcomm", True),
    ("NVIDIA GeForce GTX 680", "nvidia", False),
    ("AMD Radeon RX 570X", "amd", False),
    ("Intel HD Graphics 640", "intel", True),
    ("ARM Mali-G78 MP16", "arm", True),
    ("NVIDIA GeForce RTX 2070", "nvidia", False),
    ("Qualcomm Adreno 405", "qualcomm", True),
    ("NVIDIA GeForce GTX 670", "nvidia", False),
    ("AMD Radeon RX 480X", "amd", False),
    ("Intel HD Graphics 5800", "intel", True),
    ("ARM Mali-G52 MP16", "arm", True),
    ("NVIDIA GeForce GTX 1660", "nvidia", False),
    ("Qualcomm Adreno 312", "qualcomm", True),
    ("NVIDIA GeForce GTX 580", "nvidia", False),
    ("AMD Radeon RX 470X", "amd", False),
    ("Intel HD Graphics 560", "intel", True),
    ("ARM Mali-G51 MP1", "arm", True),
    ("NVIDIA GeForce RTX 2060 12GB", "nvidia", False),
    ("Qualcomm Adreno 225", "qualcomm", True),
    ("NVIDIA GeForce GTX 480", "nvidia", False),
    ("AMD Radeon RX 5300 XT", "amd", False),
    ("Intel HD Graphics 650", "intel", True),
    ("ARM Mali-G72 MP18", "arm", True),
    ("NVIDIA GeForce RTX 3050 6GB", "nvidia", False),
    ("Qualcomm Adreno 210", "qualcomm", True),
    ("NVIDIA GeForce GTX 280", "nvidia", False),
    ("AMD Radeon RX 560 XT", "amd", False),
    ("Intel HD Graphics P630", "intel", True),
    ("ARM Mali-G57 MP10", "arm", True),
    ("NVIDIA GeForce GTX 1650 Ti", "nvidia", False),
    ("Qualcomm Adreno 510", "qualcomm", True),
    ("NVIDIA GeForce GTX 260", "nvidia", False),
    ("AMD Radeon RX 550 640SP", "amd", False),
    ("Intel HD Graphics P530", "intel", True),
    ("ARM Mali-G68 MP6", "arm", True),
    ("NVIDIA GeForce MX330", "nvidia", False),
    ("Qualcomm Adreno 502", "qualcomm", True),
    ("NVIDIA GeForce GTX 470", "nvidia", False),
    ("AMD Radeon RX 460X", "amd", False),
    ("Intel HD Graphics P4600", "intel", True),
    ("ARM Mali-G76 MP6", "arm", True),
    ("NVIDIA GeForce MX250", "nvidia", False),
    ("Qualcomm Adreno 401", "qualcomm", True),
    ("NVIDIA GeForce GTX 570", "nvidia", False),
    ("AMD Radeon RX 6400M", "amd", False),
    ("Intel HD Graphics P580", "intel", True),
    ("ARM Mali-G78 MP12", "arm", True),
    ("NVIDIA GeForce MX150", "nvidia", False),
    ("Qualcomm Adreno 635", "qualcomm", True),
    ("NVIDIA GeForce GTX 285", "nvidia", False),
    ("AMD Radeon RX 5500 XT 8GB", "amd", False),
    ("Intel HD Graphics P4000", "intel", True),
    ("ARM Mali-G52 MP18", "arm", True),
    ("NVIDIA GeForce MX130", "nvidia", False),
    ("Qualcomm Adreno 505", "qualcomm", True),
    ("NVIDIA GeForce GTX 460", "nvidia", False),
    ("AMD Radeon RX 6750M", "amd", False),
    ("Intel HD Graphics P5200", "intel", True),
    ("ARM Mali-G57 MP12", "arm", True),
    ("NVIDIA GeForce MX110", "nvidia", False),
    ("Qualcomm Adreno 400", "qualcomm", True),
    ("NVIDIA GeForce GTX 560", "nvidia", False),
    ("AMD Radeon RX 7650M", "amd", False),
    ("Intel HD Graphics P3000", "intel", True),
    ("ARM Mali-G68 MP8", "arm", True),
    ("NVIDIA GeForce MX230", "nvidia", False),
    ("Qualcomm Adreno 325", "qualcomm", True),
    ("NVIDIA GeForce GTX 590", "nvidia", False),
    ("AMD Radeon RX 580X", "amd", False),
    ("Intel HD Graphics P5700", "intel", True),
    ("ARM Mali-G51 MP10", "arm", True),
    ("NVIDIA GeForce MX450 30.5W", "nvidia", False),
    ("Qualcomm Adreno 610", "qualcomm", True),
    ("NVIDIA GeForce GTX 690", "nvidia", False),
    ("AMD Radeon RX 570 8GB", "amd", False),
    ("Intel HD Graphics P4500", "intel", True),
    ("ARM Mali-G72 MP2", "arm", True),
    ("NVIDIA GeForce MX570 A", "nvidia", False),
    ("Qualcomm Adreno 619L", "qualcomm", True),
    ("NVIDIA GeForce GTX 295", "nvidia", False),
    ("AMD Radeon RX 5600 XT 12GB", "amd", False),
    ("Intel HD Graphics P2000", "intel", True),
    ("ARM Mali-G78 MP8", "arm", True),
    ("NVIDIA GeForce GTX 780M", "nvidia", False),
    ("Qualcomm Adreno 7c", "qualcomm", True),
    ("NVIDIA GeForce GTX 465", "nvidia", False),
    ("AMD Radeon RX 5500 XT 4GB", "amd", False),
    ("Intel HD Graphics P1000", "intel", True),
    ("ARM Mali-G52 MP20", "arm", True),
    ("NVIDIA GeForce GTX 770M", "nvidia", False),
    ("Qualcomm Adreno 8c", "qualcomm", True),
    ("NVIDIA GeForce GTX 555", "nvidia", False),
    ("AMD Radeon RX 6500 XT 4GB", "amd", False),
    ("Intel UHD Graphics P630", "intel", True),
    ("ARM Mali-G68 MP10", "arm", True),
    ("NVIDIA GeForce GTX 675M", "nvidia", False),
    ("Qualcomm Adreno 619 5G", "qualcomm", True),
    ("NVIDIA GeForce GTX 560 Ti", "nvidia", False),
    ("AMD Radeon RX 6600 XT 8GB", "amd", False),
    ("Intel Iris Pro Graphics 580", "intel", True),
    ("ARM Mali-G76 MP10", "arm", True),
    ("NVIDIA GeForce GTX 680M", "nvidia", False),
    ("Qualcomm Adreno X1-54", "qualcomm", True),
    ("NVIDIA GeForce GTX 580M", "nvidia", False),
    ("AMD Radeon RX 6700 XT 12GB", "amd", False),
    ("Intel Iris Pro Graphics 5200", "intel", True),
    ("ARM Mali-G78 MP18", "arm", True),
    ("NVIDIA GeForce GTX 680MX", "nvidia", False),
    ("Qualcomm Adreno X1-44", "qualcomm", True),
    ("NVIDIA GeForce GTX 485M", "nvidia", False),
    ("AMD Radeon RX 6800 XT 16GB", "amd", False),
    ("Intel Iris Pro Graphics 6200", "intel", True),
    ("ARM Mali-G52 MP24", "arm", True),
    ("NVIDIA GeForce GTX 775M", "nvidia", False),
    ("Qualcomm Adreno X1-26", "qualcomm", True),
    ("NVIDIA GeForce GTX 460M", "nvidia", False),
    ("AMD Radeon RX 7900 GRE 16GB", "amd", False),
    ("Intel Iris Pro Graphics 6100", "intel", True),
    ("ARM Mali-G57 MP16", "arm", True),
    ("NVIDIA GeForce GTX 660M", "nvidia", False),
    ("Qualcomm Adreno X1-55", "qualcomm", True),
    ("NVIDIA GeForce GTX 670M", "nvidia", False),
    ("AMD Radeon RX 7600 XT 16GB", "amd", False),
    ("Intel Iris Graphics 6000", "intel", True),
    ("ARM Mali-G68 MP12", "arm", True),
    ("NVIDIA GeForce GTX 570M", "nvidia", False),
    ("Qualcomm Adreno X1-35", "qualcomm", True),
    ("NVIDIA GeForce GTX 485", "nvidia", False),
    ("AMD Radeon RX 6950 XT 16GB", "amd", False),
    ("Intel Iris Graphics 5400", "intel", True),
    ("ARM Mali-G51 MP12", "arm", True),
    ("NVIDIA GeForce GTX 560M", "nvidia", False),
    ("Qualcomm Adreno X1-32", "qualcomm", True),
    ("NVIDIA GeForce GTX 470M", "nvidia", False),
    ("AMD Radeon RX 7800 XT 16GB", "amd", False),
    ("Intel Iris Graphics 5500", "intel", True),
    ("ARM Mali-G72 MP24", "arm", True),
    ("NVIDIA GeForce GTX 480M", "nvidia", False),
    ("Qualcomm Adreno X1-28", "qualcomm", True),
    ("NVIDIA GeForce GTX 285M", "nvidia", False),
    ("AMD Radeon RX 7700 XT 12GB", "amd", False),
    ("Intel Iris Graphics 6400", "intel", True),
    ("ARM Mali-G57 MP20", "arm", True),
    ("NVIDIA GeForce GTX 260M", "nvidia", False),
    ("ARM Mali-G76 MP14", "arm", True),
    ("Qualcomm Adreno X1-42", "qualcomm", True),
    ("NVIDIA GeForce GTX 295M", "nvidia", False),
    ("AMD Radeon RX 7900 XT 20GB", "amd", False),
    ("Intel Iris Graphics 6500", "intel", True),
    ("ARM Mali-G52 MP28", "arm", True),
    ("NVIDIA GeForce GTX 460 SE", "nvidia", False),
    ("Qualcomm Adreno X1-24", "qualcomm", True),
    ("NVIDIA GeForce GTX 465M", "nvidia", False),
    ("AMD Radeon RX 6800 16GB", "amd", False),
    ("Intel Iris Graphics 6600", "intel", True),
    ("ARM Mali-G57 MP24", "arm", True),
    ("NVIDIA GeForce GTX 550 Ti", "nvidia", False),
    ("Qualcomm Adreno X1-22", "qualcomm", True),
    ("NVIDIA GeForce GTX 555M", "nvidia", False),
    ("AMD Radeon RX 6700 10GB", "amd", False),
    ("Intel Iris Graphics 6700", "intel", True),
    ("ARM Mali-G68 MP16", "arm", True),
    ("NVIDIA GeForce GTX 690M", "nvidia", False),
    ("Qualcomm Adreno X1-20", "qualcomm", True),
    ("NVIDIA GeForce GTX 460 v2", "nvidia", False),
    ("AMD Radeon RX 6600 8GB", "amd", False),
    ("Intel Iris Graphics 6800", "intel", True),
    ("ARM Mali-G51 MP16", "arm", True),
    ("NVIDIA GeForce GTX 650 Ti", "nvidia", False),
    ("Qualcomm Adreno X1-18", "qualcomm", True),
    ("NVIDIA GeForce GTX 560 Ti 448", "nvidia", False),
    ("AMD Radeon RX 6500 4GB", "amd", False),
    ("Intel Iris Graphics 6900", "intel", True),
    ("ARM Mali-G72 MP6", "arm", True),
    ("NVIDIA GeForce GTX 760 192-bit", "nvidia", False),
    ("Qualcomm Adreno X1-16", "qualcomm", True),
    ("NVIDIA GeForce GTX 650 Ti Boost", "nvidia", False),
    ("AMD Radeon RX 6400 4GB", "amd", False),
    ("Intel Iris Graphics 7000", "intel", True),
    ("ARM Mali-G76 MP18", "arm", True),
    ("NVIDIA GeForce GTX 780 6GB", "nvidia", False),
    ("Qualcomm Adreno X1-14", "qualcomm", True),
    ("NVIDIA GeForce GTX 670 MX", "nvidia", False),
    ("AMD Radeon RX 6300 2GB", "amd", False),
    ("Intel Iris Graphics 7100", "intel", True),
    ("ARM Mali-G52 MP32", "arm", True),
    ("NVIDIA GeForce GTX 770 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-12", "qualcomm", True),
    ("NVIDIA GeForce GTX 580 3GB", "nvidia", False),
    ("AMD Radeon RX 550 4GB", "amd", False),
    ("Intel Iris Graphics 7200", "intel", True),
    ("ARM Mali-G57 MP28", "arm", True),
    ("NVIDIA GeForce GTX 680 4GB", "nvidia", False),
    ("Qualcomm Adreno X1-10", "qualcomm", True),
    ("NVIDIA GeForce GTX 480 1.5GB", "nvidia", False),
    ("AMD Radeon RX 560 4GB", "amd", False),
    ("Intel Iris Graphics 7300", "intel", True),
    ("ARM Mali-G68 MP20", "arm", True),
    ("NVIDIA GeForce GTX 570 1.25GB", "nvidia", False),
    ("Qualcomm Adreno X1-08", "qualcomm", True),
    ("NVIDIA GeForce GTX 460 768MB", "nvidia", False),
    ("AMD Radeon RX 470 4GB", "amd", False),
    ("Intel Iris Graphics 7400", "intel", True),
    ("ARM Mali-G51 MP20", "arm", True),
    ("NVIDIA GeForce GTX 560 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-06", "qualcomm", True),
    ("NVIDIA GeForce GTX 275", "nvidia", False),
    ("AMD Radeon RX 480 4GB", "amd", False),
    ("Intel Iris Graphics 7500", "intel", True),
    ("ARM Mali-G72 MP8", "arm", True),
    ("NVIDIA GeForce GTX 285 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-04", "qualcomm", True),
    ("NVIDIA GeForce GTX 260 216", "nvidia", False),
    ("AMD Radeon RX 580 4GB", "amd", False),
    ("Intel Iris Graphics 7600", "intel", True),
    ("ARM Mali-G76 MP20", "arm", True),
    ("NVIDIA GeForce GTX 280 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-02", "qualcomm", True),
    ("NVIDIA GeForce GTX 295 1792MB", "nvidia", False),
    ("AMD Radeon RX 570 4GB", "amd", False),
    ("Intel Iris Graphics 7700", "intel", True),
    ("ARM Mali-G52 MP36", "arm", True),
    ("NVIDIA GeForce GTX 275 896MB", "nvidia", False),
    ("Qualcomm Adreno X1-01", "qualcomm", True),
    ("NVIDIA GeForce GTX 465 1GB", "nvidia", False),
    ("AMD Radeon RX 460 2GB", "amd", False),
    ("Intel Iris Graphics 7800", "intel", True),
    ("ARM Mali-G57 MP32", "arm", True),
    ("NVIDIA GeForce GTX 555 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-09", "qualcomm", True),
    ("NVIDIA GeForce GTX 470 1280MB", "nvidia", False),
    ("AMD Radeon RX 5500 8GB", "amd", False),
    ("Intel Iris Graphics 7900", "intel", True),
    ("ARM Mali-G68 MP24", "arm", True),
    ("NVIDIA GeForce GTX 480 1536MB", "nvidia", False),
    ("Qualcomm Adreno X1-21", "qualcomm", True),
    ("NVIDIA GeForce GTX 560 Ti 1GB", "nvidia", False),
    ("AMD Radeon RX 5600 6GB", "amd", False),
    ("Intel Iris Graphics 8000", "intel", True),
    ("ARM Mali-G51 MP24", "arm", True),
    ("NVIDIA GeForce GTX 570 1280MB", "nvidia", False),
    ("Qualcomm Adreno X1-30", "qualcomm", True),
    ("NVIDIA GeForce GTX 580 1536MB", "nvidia", False),
    ("AMD Radeon RX 5500M 4GB", "amd", False),
    ("Intel Iris Graphics 8100", "intel", True),
    ("ARM Mali-G72 MP10", "arm", True),
    ("NVIDIA GeForce GTX 590 3072MB", "nvidia", False),
    ("Qualcomm Adreno X1-31", "qualcomm", True),
    ("NVIDIA GeForce GTX 560 SE", "nvidia", False),
    ("AMD Radeon RX 5300M 3GB", "amd", False),
    ("Intel Iris Graphics 8200", "intel", True),
    ("ARM Mali-G76 MP22", "arm", True),
    ("NVIDIA GeForce GTX 650 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-33", "qualcomm", True),
    ("NVIDIA GeForce GTX 750 1GB", "nvidia", False),
    ("AMD Radeon RX 540 2GB", "amd", False),
    ("Intel Iris Graphics 8300", "intel", True),
    ("ARM Mali-G52 MP40", "arm", True),
    ("NVIDIA GeForce GTX 760 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-34", "qualcomm", True),
    ("NVIDIA GeForce GTX 770 4GB", "nvidia", False),
    ("AMD Radeon RX 550X 4GB", "amd", False),
    ("Intel Iris Graphics 8400", "intel", True),
    ("ARM Mali-G57 MP36", "arm", True),
    ("NVIDIA GeForce GTX 780 Ti 3GB", "nvidia", False),
    ("Qualcomm Adreno X1-36", "qualcomm", True),
    ("NVIDIA GeForce GTX 660 Ti", "nvidia", False),
    ("AMD Radeon RX 560X 4GB", "amd", False),
    ("Intel Iris Graphics 8500", "intel", True),
    ("ARM Mali-G68 MP28", "arm", True),
    ("NVIDIA GeForce GTX 670 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-37", "qualcomm", True),
    ("NVIDIA GeForce GTX 680 2GB", "nvidia", False),
    ("AMD Radeon RX 570X 8GB", "amd", False),
    ("Intel Iris Graphics 8600", "intel", True),
    ("ARM Mali-G51 MP28", "arm", True),
    ("NVIDIA GeForce GTX 690 4GB", "nvidia", False),
    ("Qualcomm Adreno X1-38", "qualcomm", True),
    ("NVIDIA GeForce GTX 780 3GB", "nvidia", False),
    ("AMD Radeon RX 480X 8GB", "amd", False),
    ("Intel Iris Graphics 8700", "intel", True),
    ("ARM Mali-G72 MP14", "arm", True),
    ("NVIDIA GeForce GTX 660 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-39", "qualcomm", True),
    ("NVIDIA GeForce GTX 650 2GB", "nvidia", False),
    ("AMD Radeon RX 470X 4GB", "amd", False),
    ("Intel Iris Graphics 8800", "intel", True),
    ("ARM Mali-G76 MP24", "arm", True),
    ("NVIDIA GeForce GTX 750 Ti 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-40", "qualcomm", True),
    ("NVIDIA GeForce GTX 760 Ti", "nvidia", False),
    ("AMD Radeon RX 580X 8GB", "amd", False),
    ("Intel Iris Graphics 8900", "intel", True),
    ("ARM Mali-G52 MP44", "arm", True),
    ("NVIDIA GeForce GTX 770 256-bit", "nvidia", False),
    ("Qualcomm Adreno X1-41", "qualcomm", True),
    ("NVIDIA GeForce GTX 780 Ti 6GB", "nvidia", False),
    ("AMD Radeon RX 590 8GB", "amd", False),
    ("Intel Iris Graphics 9000", "intel", True),
    ("ARM Mali-G57 MP40", "arm", True),
    ("NVIDIA GeForce GTX 750 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-43", "qualcomm", True),
    ("NVIDIA GeForce GTX 660 Ti 3GB", "nvidia", False),
    ("AMD Radeon RX 5600 XT 6GB", "amd", False),
    ("Intel Iris Graphics 9100", "intel", True),
    ("ARM Mali-G68 MP32", "arm", True),
    ("NVIDIA GeForce GTX 760 4GB", "nvidia", False),
    ("Qualcomm Adreno X1-48", "qualcomm", True),
    ("NVIDIA GeForce GTX 670 4GB", "nvidia", False),
    ("AMD Radeon RX 5700 XT 8GB", "amd", False),
    ("Intel Iris Graphics 9200", "intel", True),
    ("ARM Mali-G51 MP32", "arm", True),
    ("NVIDIA GeForce GTX 690 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-49", "qualcomm", True),
    ("NVIDIA GeForce GTX 560 Ti 2GB", "nvidia", False),
    ("AMD Radeon RX 5500 4GB", "amd", False),
    ("Intel Iris Graphics 9300", "intel", True),
    ("ARM Mali-G72 MP16", "arm", True),
    ("NVIDIA GeForce GTX 650 Ti 1GB", "nvidia", False),
    ("Qualcomm Adreno X1-50", "qualcomm", True),
    ("NVIDIA GeForce GTX 580 1.5GB", "nvidia", False),
    ("AMD Radeon RX 5600M 6GB", "amd", False),
    ("Intel Iris Graphics 9400", "intel", True),
    ("ARM Mali-G76 MP26", "arm", True),
    ("NVIDIA GeForce GTX 780 4GB", "nvidia", False),
    ("Qualcomm Adreno X1-51", "qualcomm", True),
    ("NVIDIA GeForce GTX 480 1GB", "nvidia", False),
    ("AMD Radeon RX 5700 8GB", "amd", False),
    ("Intel Iris Graphics 9500", "intel", True),
    ("ARM Mali-G52 MP48", "arm", True),
    ("NVIDIA GeForce GTX 680 1536MB", "nvidia", False),
    ("Qualcomm Adreno X1-52", "qualcomm", True),
    ("NVIDIA GeForce GTX 460 SE 1GB", "nvidia", False),
    ("AMD Radeon RX 5300 XT 3GB", "amd", False),
    ("Intel Iris Graphics 9600", "intel", True),
    ("ARM Mali-G57 MP44", "arm", True),
    ("NVIDIA GeForce GTX 590 2x", "nvidia", False),
    ("Qualcomm Adreno X1-53", "qualcomm", True),
    ("NVIDIA GeForce GTX 550 Ti 1GB", "nvidia", False),
    ("AMD Radeon RX 540X 2GB", "amd", False),
    ("Intel Iris Graphics 9700", "intel", True),
    ("ARM Mali-G68 MP36", "arm", True),
    ("NVIDIA GeForce GTX 660 3GB", "nvidia", False),
    ("Qualcomm Adreno X1-57", "qualcomm", True),
    ("NVIDIA GeForce RTX 5060 Laptop 8GB", "nvidia", False),
    ("AMD Radeon RX 7600M XT 8GB", "amd", False),
    ("Intel Iris Graphics 9900", "intel", True),
    ("ARM Mali-G78 MP32", "arm", True),
    ("Qualcomm Adreno X1-58", "qualcomm", True),
    ("Samsung Xclipse 920", "samsung", True),
    ("AMD Radeon RX 6800M 12GB", "amd", False),
    ("Intel Iris Graphics 6100 GT", "intel", True),
    ("ARM Mali-G77 MP11", "arm", True),
    ("Qualcomm Adreno X1-59", "qualcomm", True),
    ("Imagination PowerVR D-Series DXT-48-1536", "img", True),
    ("Huawei Maleoon 910", "huawei", True),
    ("NVIDIA GeForce RTX 5070 Laptop 8GB", "nvidia", False),
    ("AMD Radeon RX 6900 XT 16GB", "amd", False),
    ("Intel Arc B580 12GB", "intel", False),
    ("ARM Immortalis-G925 MP12", "arm", True),
    ("Qualcomm Adreno X1-60", "qualcomm", True),
    ("Samsung Xclipse 940", "samsung", True),
    ("NVIDIA GeForce RTX 5080 Laptop 16GB", "nvidia", False),
    ("AMD Radeon RX 7700S 8GB", "amd", False),
    ("Intel Arc A770M 16GB", "intel", False),
    ("ARM Mali-G77 MP16", "arm", True),
    ("Qualcomm Adreno X1-61", "qualcomm", True),
    ("Huawei Maleoon 920", "huawei", True),
    ("NVIDIA GeForce RTX 5090 Laptop 24GB", "nvidia", False),
    ("AMD Radeon RX 7900M 16GB", "amd", False),
    ("Intel Arc Pro B60 24GB", "intel", False),
    ("ARM Immortalis-G720 MP24", "arm", True),
    ("Qualcomm Adreno X1-62", "qualcomm", True),
    ("Samsung Xclipse 930", "samsung", True),
    ("NVIDIA GeForce RTX 5050 Laptop 8GB", "nvidia", False),
    ("AMD Radeon RX 9060 XT 16GB", "amd", False),
    ("Intel Arc B570 10GB", "intel", False),
    ("ARM Mali-G310 MP6", "arm", True),
    ("Qualcomm Adreno X1-63", "qualcomm", True),
    ("Apple M5 GPU", "apple", True),
    ("NVIDIA GeForce RTX 4050 Laptop 6GB", "nvidia", False),
    ("AMD Radeon RX 7400 8GB", "amd", False),
    ("Intel Iris Graphics 5100 GT", "intel", True),
    ("ARM Mali-G720 MP8", "arm", True),
    ("Qualcomm Adreno X1-64", "qualcomm", True),
    ("VeriSilicon Vivante GC8000", "vivante", True),
    ("NVIDIA GeForce RTX 3050 Ti Laptop 4GB", "nvidia", False),
    ("AMD Radeon RX 7600M 8GB", "amd", False),
    ("NVIDIA GeForce GTX 285 2GB", "nvidia", False),
    ("ARM Panfrost Mali-G52", "arm", True),
    ("Qualcomm Adreno X1-65", "qualcomm", True),
    ("ARM Panthor Mali-G720", "arm", True),
    ("NVIDIA GeForce RTX 2050 Laptop 4GB", "nvidia", False),
    ("AMD Radeon RX 6550M 4GB", "amd", False),
    ("Intel Arc A350M 4GB", "intel", False),
    ("ARM Mali-G510 MP6", "arm", True),
    ("Qualcomm Adreno X1-66", "qualcomm", True),
    ("Samsung Xclipse 920 Lite", "samsung", True),
    ("NVIDIA GeForce MX570 2GB", "nvidia", False),
    ("AMD Radeon RX 6450M 2GB", "amd", False),
    ("Intel Arc A370M 4GB", "intel", False),
    ("ARM Mali-400 MP4", "arm", True),
    ("Qualcomm Adreno X1-67", "qualcomm", True),
    ("ARM Lima Mali-450", "arm", True),
    ("NVIDIA GeForce RTX 4060 Laptop 8GB", "nvidia", False),
    ("AMD Radeon RX 7600 8GB", "amd", False),
    ("Intel Arc A550M 8GB", "intel", False),
    ("Apple M5 Pro GPU", "apple", True),
    ("Qualcomm Adreno X1-68", "qualcomm", True),
    ("ARM Mali-G78 MP28", "arm", True),
    ("NVIDIA GeForce RTX 4070 Laptop 8GB", "nvidia", False),
    ("AMD Radeon RX 7700 12GB", "amd", False),
    ("Intel Arc A730M 12GB", "intel", False),
    ("Apple M5 Max GPU", "apple", True),
    ("Qualcomm Adreno X1-69", "qualcomm", True),
    ("Imagination PowerVR BXM-4-64", "img", True),
    ("NVIDIA GeForce RTX 4080 Laptop 12GB", "nvidia", False),
    ("AMD Radeon RX 7800M 12GB", "amd", False),
    ("Intel Arc A770 16GB", "intel", False),
    ("Apple M5 Ultra GPU", "apple", True),
    ("Qualcomm Adreno X1-70", "qualcomm", True),
    ("ARM Mali-G710 MP16", "arm", True),
    ("NVIDIA GeForce RTX 4090 Laptop 16GB", "nvidia", False),
    ("AMD Radeon RX 6750 GRE 10GB", "amd", False),
    ("Intel Arc A750 8GB", "intel", False),
    ("NVIDIA MX150 2GB", "nvidia", False),
    ("Qualcomm Adreno X1-71", "qualcomm", True),
    ("ARM Mali-G78 MP36", "arm", True),
    ("NVIDIA GeForce RTX 3050 Laptop 6GB", "nvidia", False),
    ("AMD Radeon RX 6650 XT 8GB", "amd", False),
    ("Intel Arc A580 8GB", "intel", False),
    ("Broadcom VideoCore VI", "broadcom", True),
    ("Qualcomm Adreno X1-72", "qualcomm", True),
    ("ARM Mali-G78 MP40", "arm", True),
    ("NVIDIA GeForce RTX 4070 Super 12GB", "nvidia", False),
    ("AMD Radeon RX 7600 GRE 8GB", "amd", False),
    ("Intel Arc B60 24GB", "intel", False),
    ("Moore Threads MTT S70 8GB", "moorethreads", False),
    ("Qualcomm Adreno X1-73", "qualcomm", True),
    ("ARM Mali-G78 MP44", "arm", True),
    ("NVIDIA GeForce RTX 4070 Ti Super 16GB", "nvidia", False),
    ("AMD Radeon RX 7700 GRE 12GB", "amd", False),
    ("Intel Arc Pro A60 12GB", "intel", False),
    ("Apple M4 Pro GPU 20-core", "apple", True),
    ("Qualcomm Adreno X1-74", "qualcomm", True),
    ("ARM Mali-G78 MP48", "arm", True),
    ("NVIDIA GeForce RTX 4080 Super 16GB", "nvidia", False),
    ("AMD Radeon RX 7800 GRE 16GB", "amd", False),
    ("Intel Arc Pro A40 6GB", "intel", False),
    ("Apple M4 Max GPU 40-core", "apple", True),
    ("Qualcomm Adreno X1-75", "qualcomm", True),
    ("ARM Mali-G78 MP52", "arm", True),
    ("NVIDIA GeForce RTX 4090 Super 24GB", "nvidia", False),
    ("AMD Radeon RX 7900 XTX 24GB", "amd", False),
    ("Intel Arc Pro B50 16GB", "intel", False),
    ("Apple M2 Ultra GPU 76-core", "apple", True),
    ("Qualcomm Adreno X1-76", "qualcomm", True),
    ("ARM Mali-G78 MP56", "arm", True),
    ("NVIDIA GeForce RTX 4060 Ti 8GB", "nvidia", False),
    ("AMD Radeon RX 7600 XT 8GB", "amd", False),
    ("Intel GMA 3150", "intel", True),
    ("Intel GMA 950", "intel", True),
    ("Qualcomm Adreno X1-77", "qualcomm", True),
    ("ARM Mali-G78 MP60", "arm", True),
    ("NVIDIA GeForce RTX 4070 Ti 12GB", "nvidia", False),
    ("AMD Radeon RX 9070 XT 16GB", "amd", False),
    ("Intel UHD Graphics 770E", "intel", True),
    ("Intel Arc Graphics 140T", "intel", True),
    ("Qualcomm Adreno X1-78", "qualcomm", True),
    ("ARM Mali-G78 MP64", "arm", True),
    ("NVIDIA GeForce GTX 1630 4GB", "nvidia", False),
    ("AMD Radeon RX 6400E 4GB", "amd", False),
    ("Intel UHD Graphics 750E", "intel", True),
    ("Intel Iris Xe Graphics G7 96EU", "intel", True),
    ("Qualcomm Adreno X1-79", "qualcomm", True),
    ("ARM Mali-G78 MP68", "arm", True),
    ("NVIDIA GeForce RTX 4060 Super 8GB", "nvidia", False),
    ("AMD Radeon RX 6500E 4GB", "amd", False),
    ("Intel Arc Graphics 130T", "intel", True),
    ("Intel UHD Graphics 770R", "intel", True),
    ("Qualcomm Adreno X1-80", "qualcomm", True),
    ("ARM Mali-G78 MP72", "arm", True),
    ("NVIDIA GeForce RTX 4050 6GB", "nvidia", False),
    ("AMD Radeon RX 7350 4GB", "amd", False),
    ("Intel Arc Graphics 110T", "intel", True),
    ("Intel UHD Graphics 610E", "intel", True),
    ("Qualcomm Adreno X1-81", "qualcomm", True),
    ("ARM Mali-G78 MP76", "arm", True),
    ("NVIDIA GeForce RTX 4070 Super 16GB", "nvidia", False),
    ("AMD Radeon RX 7450 8GB", "amd", False),
    ("Intel Arc Graphics 140T 8Xe", "intel", True),
    ("Intel UHD Graphics 605E", "intel", True),
    ("Qualcomm Adreno X1-82", "qualcomm", True),
    ("ARM Mali-G78 MP80", "arm", True),
    ("NVIDIA GeForce GTX 1650 Super 4GB", "nvidia", False),
    ("AMD Radeon RX 7550 8GB", "amd", False),
    ("Intel Arc Graphics 140T 7Xe", "intel", True),
    ("Intel UHD Graphics 617E", "intel", True),
    ("Qualcomm Adreno X1-83", "qualcomm", True),
    ("ARM Mali-G78 MP84", "arm", True),
    ("NVIDIA GeForce RTX 3060 Ti 8GB", "nvidia", False),
    ("AMD Radeon RX 7650 8GB", "amd", False),
    ("Intel Arc Graphics 140T 6Xe", "intel", True),
    ("Intel UHD Graphics 620E", "intel", True),
    ("Qualcomm Adreno X1-84", "qualcomm", True),
    ("ARM Mali-G78 MP88", "arm", True),
    ("NVIDIA GeForce RTX 3080 Ti 12GB", "nvidia", False),
    ("AMD Radeon RX 7750 12GB", "amd", False),
    ("Intel Arc Graphics 140T 5Xe", "intel", True),
    ("Intel UHD Graphics 630E", "intel", True),
    ("Qualcomm Adreno X1-86", "qualcomm", True),
    ("ARM Mali-G78 MP92", "arm", True),
    ("NVIDIA GeForce RTX 3090 Ti 24GB", "nvidia", False),
    ("AMD Radeon RX 7850 16GB", "amd", False),
    ("Intel Arc Graphics 140T 4Xe", "intel", True),
    ("Intel UHD Graphics 640E", "intel", True),
    ("Qualcomm Adreno X1-87", "qualcomm", True),
    ("ARM Mali-G78 MP96", "arm", True),
    ("NVIDIA GeForce RTX 3080 10GB", "nvidia", False),
    ("AMD Radeon RX 7950 24GB", "amd", False),
    ("Intel Arc Graphics 140T 3Xe", "intel", True),
    ("Intel UHD Graphics 650E", "intel", True),
    ("Qualcomm Adreno X1-88", "qualcomm", True),
    ("ARM Mali-G78 MP100", "arm", True),
    ("NVIDIA GeForce RTX 3070 Ti 8GB", "nvidia", False),
    ("AMD Radeon RX 8050 8GB", "amd", False),
    ("Intel Arc Graphics 140T 2Xe", "intel", True),
    ("Intel UHD Graphics 660E", "intel", True),
    ("Qualcomm Adreno X1-89", "qualcomm", True),
    ("ARM Mali-G78 MP104", "arm", True),
    ("NVIDIA GeForce RTX 3070 8GB", "nvidia", False),
    ("AMD Radeon RX 8150 12GB", "amd", False),
    ("Intel Arc Graphics 130T 8Xe", "intel", True),
    ("Intel UHD Graphics 670E", "intel", True),
    ("Qualcomm Adreno X1-90", "qualcomm", True),
    ("ARM Mali-G78 MP108", "arm", True),
    ("NVIDIA GeForce RTX 3050 Ti 4GB", "nvidia", False),
    ("AMD Radeon RX 8250 16GB", "amd", False),
    ("Intel Arc Graphics 130T 6Xe", "intel", True),
    ("Intel UHD Graphics 680E", "intel", True),
    ("Qualcomm Adreno X1-91", "qualcomm", True),
    ("ARM Mali-G78 MP112", "arm", True),
    ("NVIDIA GeForce RTX 4070 12GB", "nvidia", False),
    ("AMD Radeon RX 8350 16GB", "amd", False),
    ("Intel Arc Graphics 110T 8Xe", "intel", True),
    ("Intel UHD Graphics 690E", "intel", True),
    ("Qualcomm Adreno X1-92", "qualcomm", True),
    ("ARM Mali-G78 MP116", "arm", True),
    ("NVIDIA GeForce GTX 1050 Ti 4GB", "nvidia", False),
    ("AMD Radeon RX 8450 8GB", "amd", False),
    ("Intel Arc Graphics 110T 6Xe", "intel", True),
    ("Intel UHD Graphics 700E", "intel", True),
    ("Qualcomm Adreno X1-93", "qualcomm", True),
    ("ARM Mali-G78 MP120", "arm", True),
    ("NVIDIA GeForce RTX 2080 Ti 11GB", "nvidia", False),
    ("AMD Radeon RX 8550 8GB", "amd", False),
    ("Intel Arc Graphics 110T 4Xe", "intel", True),
    ("Intel UHD Graphics 710E", "intel", True),
    ("Qualcomm Adreno X1-94", "qualcomm", True),
    ("ARM Mali-G78 MP124", "arm", True),
    ("NVIDIA GeForce RTX 2080 Super 8GB", "nvidia", False),
    ("AMD Radeon RX 8650 12GB", "amd", False),
    ("Intel Arc Graphics 110T 2Xe", "intel", True),
    ("Intel UHD Graphics 720E", "intel", True),
    ("Qualcomm Adreno X1-95", "qualcomm", True),
    ("ARM Mali-G78 MP128", "arm", True),
    ("NVIDIA GeForce RTX 2070 Super 8GB", "nvidia", False),
    ("AMD Radeon RX 8750 16GB", "amd", False),
    ("Intel Arc Graphics 140T 1Xe", "intel", True),
    ("Intel UHD Graphics 730E", "intel", True),
    ("Qualcomm Adreno X1-96", "qualcomm", True),
    ("ARM Mali-G78 MP132", "arm", True),
    ("NVIDIA GeForce RTX 2060 Super 8GB", "nvidia", False),
    ("AMD Radeon RX 8850 16GB", "amd", False),
    ("Intel Arc Graphics 130T 4Xe", "intel", True),
    ("Intel UHD Graphics 740E", "intel", True),
    ("Qualcomm Adreno X1-97", "qualcomm", True),
    ("ARM Mali-G78 MP136", "arm", True),
    ("NVIDIA GeForce RTX 1660 Super 6GB", "nvidia", False),
    ("AMD Radeon RX 8950 16GB", "amd", False),
    ("Intel Arc Graphics 130T 2Xe", "intel", True),
    ("Intel UHD Graphics 750R", "intel", True),
    ("Qualcomm Adreno X1-98", "qualcomm", True),
    ("ARM Mali-G78 MP140", "arm", True),
    ("NVIDIA GeForce GTX 1660 Ti 6GB", "nvidia", False),
    ("AMD Radeon RX 9050 8GB", "amd", False),
    ("Intel Arc Graphics 130T 1Xe", "intel", True),
    ("Intel UHD Graphics 760E", "intel", True),
    ("Qualcomm Adreno X1-99", "qualcomm", True),
    ("ARM Mali-G78 MP144", "arm", True),
    ("NVIDIA GeForce GTX 1650 Ti 4GB", "nvidia", False),
    ("AMD Radeon RX 9150 8GB", "amd", False),
    ("Intel Arc Graphics 110T 1Xe", "intel", True),
    ("Intel UHD Graphics 770S", "intel", True),
    ("Qualcomm Adreno X1-100", "qualcomm", True),
    ("ARM Mali-G78 MP148", "arm", True),
    ("NVIDIA GeForce GTX 1050 2GB", "nvidia", False),
    ("AMD Radeon RX 9250 8GB", "amd", False),
    ("Intel Arc Graphics 140T 9Xe", "intel", True),
    ("Intel UHD Graphics 780E", "intel", True),
    ("Qualcomm Adreno X1-101", "qualcomm", True),
    ("ARM Mali-G78 MP152", "arm", True),
    ("NVIDIA GeForce GTX 745 1GB", "nvidia", False),
    ("AMD Radeon RX 9350 8GB", "amd", False),
    ("Intel Arc Graphics 140T 10Xe", "intel", True),
    ("Intel UHD Graphics 790E", "intel", True),
    ("Qualcomm Adreno X1-102", "qualcomm", True),
    ("ARM Mali-G78 MP156", "arm", True),
    ("NVIDIA GeForce RTX 2060 6GB", "nvidia", False),
    ("AMD Radeon RX 9450 8GB", "amd", False),
    ("Intel Arc Graphics 140T 12Xe", "intel", True),
    ("Intel UHD Graphics 800E", "intel", True),
    ("Qualcomm Adreno X1-103", "qualcomm", True),
    ("ARM Mali-G78 MP160", "arm", True),
    ("NVIDIA GeForce RTX 2070 8GB", "nvidia", False),
    ("AMD Radeon RX 9550 8GB", "amd", False),
    ("Intel Arc Graphics 140T 14Xe", "intel", True),
    ("Intel UHD Graphics 810E", "intel", True),
    ("Qualcomm Adreno X1-104", "qualcomm", True),
    ("ARM Mali-G78 MP164", "arm", True),
    ("NVIDIA GeForce RTX 2080 8GB", "nvidia", False),
    ("AMD Radeon RX 9650 12GB", "amd", False),
    ("Intel Arc Graphics 140T 16Xe", "intel", True),
    ("Intel UHD Graphics 820E", "intel", True),
    ("Qualcomm Adreno X1-105", "qualcomm", True),
    ("ARM Mali-G78 MP168", "arm", True),
    ("NVIDIA GeForce RTX 3060 8GB", "nvidia", False),
    ("AMD Radeon RX 9750 16GB", "amd", False),
    ("Intel Arc Graphics 140T 18Xe", "intel", True),
    ("Intel UHD Graphics 830E", "intel", True),
    ("Qualcomm Adreno X1-106", "qualcomm", True),
    ("ARM Mali-G78 MP172", "arm", True),
    ("NVIDIA GeForce GTX 970 4GB", "nvidia", False),
    ("AMD Radeon RX 9850 16GB", "amd", False),
    ("Intel Arc Graphics 140T 20Xe", "intel", True),
    ("Intel UHD Graphics 840E", "intel", True),
    ("Qualcomm Adreno X1-107", "qualcomm", True),
    ("ARM Mali-G78 MP176", "arm", True),
    ("NVIDIA GeForce RTX 4070 Ti 16GB", "nvidia", False),
    ("AMD Radeon RX 9950 24GB", "amd", False),
    ("Intel Arc Graphics 140T 22Xe", "intel", True),
    ("Intel UHD Graphics 850E", "intel", True),
    ("Qualcomm Adreno X1-108", "qualcomm", True),
    ("ARM Mali-G78 MP180", "arm", True),
    ("NVIDIA GeForce GTX 960 2GB", "nvidia", False),
    ("AMD Radeon RX 7050 4GB", "amd", False),
    ("Intel Arc Graphics 140T 24Xe", "intel", True),
    ("Intel UHD Graphics 860E", "intel", True),
    ("Qualcomm Adreno X1-109", "qualcomm", True),
    ("ARM Mali-G78 MP184", "arm", True),
    ("NVIDIA GeForce GTX 950 2GB", "nvidia", False),
    ("AMD Radeon RX 7150 8GB", "amd", False),
    ("Intel Arc Graphics 140T 26Xe", "intel", True),
    ("Intel UHD Graphics 870E", "intel", True),
    ("Qualcomm Adreno X1-110", "qualcomm", True),
    ("ARM Mali-G78 MP188", "arm", True),
    ("NVIDIA GeForce GTX 960 4GB", "nvidia", False),
    ("AMD Radeon RX 7250 8GB", "amd", False),
    ("Intel Arc Graphics 140T 28Xe", "intel", True),
    ("Intel UHD Graphics 880E", "intel", True),
    ("Qualcomm Adreno X1-111", "qualcomm", True),
    ("ARM Mali-G78 MP192", "arm", True),
    ("NVIDIA GeForce GTX 980 4GB", "nvidia", False),
    ("AMD Radeon RX 7350 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 32Xe", "intel", True),
    ("Intel UHD Graphics 890E", "intel", True),
    ("Qualcomm Adreno X1-112", "qualcomm", True),
    ("ARM Mali-G78 MP196", "arm", True),
    ("NVIDIA GeForce GTX 980 Ti 6GB", "nvidia", False),
    ("AMD Radeon RX 7450 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 36Xe", "intel", True),
    ("Intel UHD Graphics 900E", "intel", True),
    ("Qualcomm Adreno X1-113", "qualcomm", True),
    ("ARM Mali-G78 MP200", "arm", True),
    ("NVIDIA GeForce GTX 970 3.5GB", "nvidia", False),
    ("AMD Radeon RX 7550 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 40Xe", "intel", True),
    ("Intel UHD Graphics 910E", "intel", True),
    ("Qualcomm Adreno X1-114", "qualcomm", True),
    ("ARM Mali-G78 MP204", "arm", True),
    ("NVIDIA GeForce GTX 980 6GB", "nvidia", False),
    ("AMD Radeon RX 7650 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 44Xe", "intel", True),
    ("Intel UHD Graphics 920E", "intel", True),
    ("Qualcomm Adreno X1-115", "qualcomm", True),
    ("ARM Mali-G78 MP208", "arm", True),
    ("NVIDIA GeForce GTX 970 6GB", "nvidia", False),
    ("AMD Radeon RX 7750 XT 12GB", "amd", False),
    ("Intel Arc Graphics 140T 48Xe", "intel", True),
    ("Intel UHD Graphics 930E", "intel", True),
    ("Qualcomm Adreno X1-116", "qualcomm", True),
    ("ARM Mali-G78 MP212", "arm", True),
    ("NVIDIA GeForce RTX 3080 20GB", "nvidia", False),
    ("AMD Radeon RX 7850 XT 16GB", "amd", False),
    ("Intel Arc Graphics 140T 52Xe", "intel", True),
    ("Intel UHD Graphics 940E", "intel", True),
    ("Qualcomm Adreno X1-117", "qualcomm", True),
    ("ARM Mali-G78 MP216", "arm", True),
    ("NVIDIA GeForce RTX 3090 24GB", "nvidia", False),
    ("AMD Radeon RX 7950 XT 24GB", "amd", False),
    ("Intel Arc Graphics 140T 56Xe", "intel", True),
    ("Intel UHD Graphics 950E", "intel", True),
    ("Qualcomm Adreno X1-118", "qualcomm", True),
    ("ARM Mali-G78 MP220", "arm", True),
    ("NVIDIA GeForce RTX 4090 D 24GB", "nvidia", False),
    ("AMD Radeon RX 8050 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 64Xe", "intel", True),
    ("Intel UHD Graphics 960E", "intel", True),
    ("Qualcomm Adreno X1-119", "qualcomm", True),
    ("ARM Mali-G78 MP224", "arm", True),
    ("NVIDIA GeForce RTX 4070 8GB", "nvidia", False),
    ("AMD Radeon RX 8150 XT 12GB", "amd", False),
    ("Intel Arc Graphics 140T 72Xe", "intel", True),
    ("Intel UHD Graphics 970E", "intel", True),
    ("Qualcomm Adreno X1-120", "qualcomm", True),
    ("ARM Mali-G78 MP228", "arm", True),
    ("NVIDIA GeForce RTX 4060 Ti 16GB", "nvidia", False),
    ("AMD Radeon RX 8250 XT 16GB", "amd", False),
    ("Intel Arc Graphics 140T 80Xe", "intel", True),
    ("Intel UHD Graphics 980E", "intel", True),
    ("Qualcomm Adreno X1-121", "qualcomm", True),
    ("ARM Mali-G78 MP232", "arm", True),
    ("NVIDIA GeForce RTX 4050 Ti 6GB", "nvidia", False),
    ("AMD Radeon RX 8350 XT 16GB", "amd", False),
    ("Intel Arc Graphics 140T 96Xe", "intel", True),
    ("Intel UHD Graphics 990E", "intel", True),
    ("Qualcomm Adreno X1-122", "qualcomm", True),
    ("ARM Mali-G78 MP236", "arm", True),
    ("NVIDIA GeForce RTX 5050 8GB", "nvidia", False),
    ("AMD Radeon RX 8450 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 112Xe", "intel", True),
    ("Intel UHD Graphics 995E", "intel", True),
    ("Qualcomm Adreno X1-123", "qualcomm", True),
    ("ARM Mali-G78 MP240", "arm", True),
    ("NVIDIA GeForce RTX 5060 8GB", "nvidia", False),
    ("AMD Radeon RX 8550 XT 8GB", "amd", False),
    ("Intel Arc Graphics 140T 128Xe", "intel", True),
    ("Intel UHD Graphics 999E", "intel", True),
    ("Qualcomm Adreno X1-124", "qualcomm", True),
    ("ARM Mali-G78 MP244", "arm", True),
    ("NVIDIA GeForce RTX 5070 12GB", "nvidia", False),
    ("AMD Radeon RX 8650 XT 12GB", "amd", False),
    ("Intel Arc Graphics 140T 144Xe", "intel", True),
    ("Intel UHD Graphics 610R", "intel", True),
    ("Qualcomm Adreno X1-125", "qualcomm", True),
    ("ARM Mali-G78 MP248", "arm", True),
)

def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass

def _configure_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _enable_windows_vt()

_LIVE_STATUS_ACTIVE = False
_LIVE_STATUS_ROWS = 0

_METEOR_ART = (
    "███╗   ███╗███████╗████████╗███████╗ ██████╗ ██████╗",
    "████╗ ████║██╔════╝╚══██╔══╝██╔════╝██╔═══██╗██╔══██╗",
    "██╔████╔██║█████╗     ██║   █████╗  ██║   ██║██████╔╝",
    "██║╚██╔╝██║██╔══╝     ██║   ██╔══╝  ██║   ██║██╔══██╗",
    "██║ ╚═╝ ██║███████╗   ██║   ███████╗╚██████╔╝██║  ██║",
    "╚═╝     ╚═╝╚══════╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═╝  ╚═╝",
)

def meteor_banner() -> str:
    inner = max(len(row) for row in _METEOR_ART)
    width = inner + 4
    stars = "          ˚     ✦      .     ⋆      ˚     ✦"
    top = "╭" + "─" * width + "╮"
    bot = "╰" + "─" * width + "╯"
    lines = [stars, top]
    for row in _METEOR_ART:
        lines.append("│  " + row.ljust(inner) + "  │")
    streak = ("✦  ───═════⬤").center(inner)
    lines.append("│  " + streak + "  │")
    lines.append(bot)
    return "\n".join(lines)

def short_gpu_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip())
    if not text:
        return "-"
    m = re.search(r"(RTX\s*\d+(?:\s*(?:Ti|SUPER|Laptop))?)", text, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).replace(" Laptop", "").strip()
    m = re.search(r"(RX\s*\d+\s*(?:XT|GRE)?)", text, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(\d{3,4}M)\b", text)
    if m:
        return m.group(1)
    trimmed = (
        text.replace("NVIDIA GeForce ", "")
        .replace("NVIDIA ", "")
        .replace("AMD Radeon ", "")
        .replace(" Graphics", "")
    )
    return trimmed[:18] if trimmed else "-"

def short_work_name(name: str) -> str:
    base = Path((name or "").replace("\\", "/")).name or (name or "-")
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base or "-"

def end_live_status() -> None:
    global _LIVE_STATUS_ACTIVE, _LIVE_STATUS_ROWS
    if not _LIVE_STATUS_ACTIVE and _LIVE_STATUS_ROWS <= 0:
        return
    try:
        sys.stdout.write("\033[?7h\n")
        sys.stdout.flush()
    except Exception:
        pass
    _LIVE_STATUS_ACTIVE = False
    _LIVE_STATUS_ROWS = 0

def display_gpu_slots(sample: SystemSample) -> List[Tuple[str, str]]:
    slots: List[Tuple[str, str]] = []

    def _add(gpu: GpuSample) -> None:
        if not gpu.present:
            return
        name = short_gpu_name(gpu.name) if gpu.name else "-"
        pct = "-" if gpu.util_pct is None else f"{gpu.util_pct:.0f}"
        slots.append((name, pct))

    if sample.igpu.present:
        _add(sample.igpu)
    if sample.rtx.present:
        _add(sample.rtx)
    while len(slots) < 2:
        slots.append(("-", "-"))
    return slots[:2]

def format_gpu_list(sample: SystemSample) -> str:
    names = [name for name, _pct in display_gpu_slots(sample) if name != "-"]
    return ", ".join(names) if names else "-"

def format_live_status(sample: SystemSample, decision: Optional[PolicyDecision] = None) -> str:
    work_name = "idle"
    if decision is not None and (decision.target_name or decision.target_exe):
        work_name = short_work_name(decision.target_name or decision.target_exe)
    else:
        work = infer_workload(sample)
        if work.kind == "idle":
            work_name = "idle"
        elif work.target is not None:
            work_name = short_work_name(work.target.name)
        else:
            work_name = work.kind
    g0, g1 = display_gpu_slots(sample)
    used = max(0, sample.memory.total_bytes - sample.memory.avail_bytes)
    ram_used = used / float(1024 ** 3)
    ram_total = sample.memory.total_bytes / float(1024 ** 3)
    return (
        f"Main Task : {work_name}, "
        f"GPU 0 ({g0[0]}) : {g0[1]}%, "
        f"GPU 1 ({g1[0]}) : {g1[1]}%, "
        f"RAM : {ram_used:.1f}GB / {ram_total:.1f}GB"
    )

def format_meteor_intro(sample: SystemSample) -> str:
    gpus = format_gpu_list(sample)
    return "\n".join(
        [
            meteor_banner(),
            "",
            "GPU Boosting Software 'METEOR'",
            f"GPU(s) : {gpus}",
            f"Policy Version : {POLICY_REVISION} (latest)",
            "Ctrl + C asks Exit?",
            "The boost table refreshes every 5 seconds",
            "",
        ]
    )

def print_meteor_intro(sample: SystemSample) -> None:
    if os.environ.get("HYBRID_QUIET") == "1":
        return
    try:
        sys.stdout.write(format_meteor_intro(sample) + "\n")
        sys.stdout.flush()
    except Exception:
        pass

def print_live_status(block: str) -> None:
    global _LIVE_STATUS_ACTIVE, _LIVE_STATUS_ROWS
    if os.environ.get("HYBRID_QUIET") == "1":
        return
    lines = block.splitlines() or [""]
    try:
        sys.stdout.write("\033[?7l")
        if _LIVE_STATUS_ACTIVE:
            sys.stdout.write("\r\033[2K")
            for _ in range(max(0, _LIVE_STATUS_ROWS - 1)):
                sys.stdout.write("\033[1A\033[2K")
        else:
            sys.stdout.write("\r\033[2K")
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
    except Exception:
        return
    _LIVE_STATUS_ACTIVE = True
    _LIVE_STATUS_ROWS = len(lines)


SELECT_HIGHLIGHT = "\033[30;43m"
SELECT_RESET = "\033[0m"
VENDOR_LABELS = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "apple": "Apple",
    "qualcomm": "Qualcomm",
    "arm": "ARM",
    "img": "Imagination",
    "broadcom": "Broadcom",
    "moorethreads": "Moore Threads",
    "samsung": "Samsung",
    "huawei": "Huawei",
    "other": "GPU",
}


def stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def host_os_name() -> str:
    if os.name == "nt":
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux"


def order_display_gpus(gpus: Sequence[GpuSample]) -> List[GpuSample]:
    live: List[GpuSample] = []
    seen = set()
    for gpu in gpus:
        if not gpu.present or looks_skip_adapter(gpu.name):
            continue
        key = (re.sub(r"\s+", " ", (gpu.name or "").lower()).strip(), gpu.vendor)
        if key in seen:
            continue
        seen.add(key)
        live.append(gpu)
    live.sort(
        key=lambda g: (
            0 if (g.discrete is False or looks_integrated(g.name, g.vram_total_mb)) else 1,
            -discrete_score(g),
            g.name,
        )
    )
    return live


def listed_gpus(sample: SystemSample) -> List[GpuSample]:
    if sample.adapters:
        return [g for g in sample.adapters if g.present]
    out: List[GpuSample] = []
    if sample.igpu.present:
        out.append(sample.igpu)
    if sample.rtx.present:
        out.append(sample.rtx)
    return out


def format_gpu_check(sample: SystemSample) -> str:
    gpus = listed_gpus(sample)
    lines = ["GPU check"]
    if not gpus:
        lines.append("  No GPU found")
        return "\n".join(lines)
    for idx, gpu in enumerate(gpus):
        lines.append(f"  GPU {idx}: {gpu.name}")
    return "\n".join(lines)


def format_recovery_manual(sample: SystemSample, os_name: Optional[str] = None) -> str:
    os_label = os_name or host_os_name()
    vendors = []
    for gpu in listed_gpus(sample):
        label = VENDOR_LABELS.get(gpu.vendor, gpu.vendor.upper() or "GPU")
        if label not in vendors:
            vendors.append(label)
    if not vendors:
        vendors = ["GPU"]
    vendor_join = " + ".join(vendors)
    lines = [
        f"Recovery manual ({os_label} / {vendor_join})",
        "",
        "METEOR restores GpuPreference, power limits, and clock locks on a clean exit.",
        "If a write fails it aborts and restores the snapshot immediately.",
        "Ctrl+C during boost asks Exit?  Yes prints Quitting... and restores.",
        "",
    ]
    if os_label == "Windows":
        lines.extend(
            [
                "Windows",
                "- GpuPreference is HKCU only: Software\\Microsoft\\DirectX\\UserGpuPreferences",
                "- Fully quit the game or sim, then start it again so it rereads the preference.",
                "- Task Manager > Performance > GPU 0 / GPU 1 to confirm which adapter is drawing.",
                "",
            ]
        )
        if "NVIDIA" in vendors:
            lines.extend(
                [
                    "NVIDIA",
                    "- Admin PowerShell: nvidia-smi -rgc && nvidia-smi -rmc  (clear clock locks)",
                    "- nvidia-smi -pl <TGP> if a power limit stayed behind.",
                    "- If a game is still on GPU 0, quit it completely and relaunch after METEOR pins GpuPreference=2.",
                    "",
                ]
            )
        if "AMD" in vendors:
            lines.extend(
                [
                    "AMD",
                    "- AMD Software: Adrenalin Edition > Performance > Reset to default.",
                    "- If 780M stays at 100% and RTX is idle, the 3D app is on the iGPU: quit it, then relaunch.",
                    "",
                ]
            )
        if "Intel" in vendors:
            lines.extend(
                [
                    "Intel",
                    "- Intel Graphics Command Center > restore defaults, or reboot.",
                    "",
                ]
            )
    elif os_label == "macOS":
        lines.extend(
            [
                "macOS",
                "- METEOR does not write Windows GpuPreference on macOS.",
                "- If Metal is stuck, quit the app. Reboot if the GPU stays pinned.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Linux",
                "- METEOR may set sysfs performance levels. A clean exit writes them back.",
                "",
            ]
        )
        if "NVIDIA" in vendors:
            lines.extend(
                [
                    "NVIDIA",
                    "- nvidia-smi -rgc && nvidia-smi -rmc",
                    "- nvidia-smi -pl <TGP> if needed",
                    "",
                ]
            )
        if "AMD" in vendors:
            lines.extend(
                [
                    "AMD",
                    "- echo auto | sudo tee /sys/class/drm/card*/device/power_dpm_force_performance_level",
                    "",
                ]
            )
    lines.append("If restore still looks wrong, reboot once. That clears driver clocks and process GPU bindings.")
    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    raw = text or "-"
    if len(raw) > width:
        return raw[: max(1, width - 1)] + "…"
    return raw.ljust(width)


def format_boost_table(sample: SystemSample, decision: Optional[PolicyDecision] = None) -> str:
    gpus = listed_gpus(sample)
    work = "idle"
    mode = "-"
    if decision is not None:
        mode = decision.mode
        if decision.target_name or decision.target_exe:
            work = short_work_name(decision.target_name or decision.target_exe)
        else:
            inferred = infer_workload(sample)
            work = short_work_name(inferred.target.name) if inferred.target else inferred.kind
    used = max(0, sample.memory.total_bytes - sample.memory.avail_bytes)
    ram_used = used / float(1024 ** 3)
    ram_total = sample.memory.total_bytes / float(1024 ** 3)
    head = "| GPU | Name                   | Util   | Memory         | Temp  | Pwr / Clock      |"
    mid = "+-----+------------------------+--------+----------------+-------+------------------+"
    width = max(len(head), len(mid))
    bar = "+" + "-" * (width - 2) + "+"
    mid = _pad(mid[:-1], width - 1) + "+"
    head = _pad(head[:-1], width - 1) + "|"
    title = (
        f"| METEOR {POLICY_REVISION}  Mode {mode:<6}  Task {_pad(work, 14)}"
        f"  RAM {ram_used:4.1f}/{ram_total:4.1f} GB"
    )
    title = _pad(title, width - 1) + "|"
    rows = [bar, title, bar, head, mid]
    if not gpus:
        empty = "|   - | " + _pad("No GPU found", 22) + " |      - |              - |     - |                - |"
        rows.append(_pad(empty[:-1], width - 1) + "|")
    for idx, gpu in enumerate(gpus):
        util = "-" if gpu.util_pct is None else f"{gpu.util_pct:.0f} %"
        if gpu.vram_used_mb is None and gpu.vram_total_mb is None:
            mem = "-"
        else:
            left = "-" if gpu.vram_used_mb is None else f"{gpu.vram_used_mb:.0f}"
            right = "-" if gpu.vram_total_mb is None else f"{gpu.vram_total_mb:.0f}"
            mem = f"{left}/{right} MB"
        temp = "-" if gpu.temp_c is None else f"{gpu.temp_c:.0f} C"
        pwr = "-" if gpu.power_w is None else f"{gpu.power_w:.0f}W"
        clk = "-" if gpu.clock_mhz is None else f"{gpu.clock_mhz:.0f}"
        pwclk = f"{pwr} / {clk}"
        row = (
            f"|  {idx:<2} | {_pad(short_gpu_name(gpu.name), 22)} | {_pad(util, 6)} | {_pad(mem, 14)} |"
            f" {_pad(temp, 5)} | {_pad(pwclk, 16)} |"
        )
        rows.append(_pad(row[:-1], width - 1) + "|")
    rows.append(mid)
    return "\n".join(rows)


def render_select(title: str, options: Sequence[str], index: int) -> str:
    lines = [title, ""]
    for i, opt in enumerate(options):
        label = f"{i + 1}. {opt}"
        if i == index:
            lines.append(f"  {SELECT_HIGHLIGHT} {label} {SELECT_RESET}")
        else:
            lines.append(f"    {label}")
    lines.append("")
    lines.append("Arrow keys move, Enter selects")
    return "\n".join(lines)


def _write_menu(out: TextIO, block: str, prev_rows: int) -> int:
    lines = block.splitlines() or [""]
    try:
        if prev_rows > 0:
            out.write("\r\033[2K")
            for _ in range(max(0, prev_rows - 1)):
                out.write("\033[1A\033[2K")
        out.write("\n".join(lines))
        if hasattr(out, "flush"):
            out.flush()
    except Exception:
        pass
    return len(lines)


def read_key() -> str:
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            extra = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(extra, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x1b":
            return "esc"
        return ch
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, when=termios.TCSANOW)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            extra = ""
            try:
                import select as _sel
                ready, _, _ = _sel.select([sys.stdin], [], [], 0.05)
                if ready:
                    extra = sys.stdin.read(2)
            except Exception:
                extra = sys.stdin.read(2)
            if extra.startswith("[A"):
                return "up"
            if extra.startswith("[B"):
                return "down"
            if extra.startswith("[C"):
                return "right"
            if extra.startswith("[D"):
                return "left"
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        if ch == "\x1a":
            return "esc"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_choice(
    title: str,
    options: Sequence[str],
    *,
    default: int = 0,
    key_reader: Optional[Callable[[], str]] = None,
    out: Optional[TextIO] = None,
) -> int:
    if not options:
        return 0
    index = max(0, min(int(default), len(options) - 1))
    stream = out if out is not None else sys.stdout
    reader = key_reader if key_reader is not None else (read_key if stdin_is_interactive() else None)
    if reader is None:
        stream.write(render_select(title, options, index) + "\n")
        if hasattr(stream, "flush"):
            stream.flush()
        return index
    prev = 0
    while True:
        block = render_select(title, options, index)
        prev = _write_menu(stream, block, prev)
        key = reader()
        if key == "up" or key == "left":
            index = (index - 1) % len(options)
        elif key == "down" or key == "right":
            index = (index + 1) % len(options)
        elif key == "enter":
            stream.write("\n")
            if hasattr(stream, "flush"):
                stream.flush()
            return index
        elif key in {"esc", "ctrl-c"}:
            stream.write("\n")
            if hasattr(stream, "flush"):
                stream.flush()
            raise KeyboardInterrupt
        elif key.isdigit():
            picked = int(key) - 1
            if 0 <= picked < len(options):
                index = picked
                prev = _write_menu(stream, render_select(title, options, index), prev)
                stream.write("\n")
                if hasattr(stream, "flush"):
                    stream.flush()
                return index


def confirm_yes_no(
    question: str,
    *,
    default_yes: bool = True,
    key_reader: Optional[Callable[[], str]] = None,
    out: Optional[TextIO] = None,
) -> bool:
    idx = select_choice(
        question,
        ["Yes", "No"],
        default=0 if default_yes else 1,
        key_reader=key_reader,
        out=out,
    )
    return idx == 0


def ask_exit(
    *,
    key_reader: Optional[Callable[[], str]] = None,
    out: Optional[TextIO] = None,
) -> bool:
    try:
        return confirm_yes_no("Exit?", default_yes=True, key_reader=key_reader, out=out)
    except KeyboardInterrupt:
        return True


def print_quitting(out: Optional[TextIO] = None) -> None:
    end_live_status()
    stream = out if out is not None else sys.stdout
    try:
        stream.write("Quitting...\n")
        if hasattr(stream, "flush"):
            stream.flush()
    except Exception:
        pass


def run_startup_wizard(
    sample: SystemSample,
    *,
    auto: bool = False,
    rescan: Optional[Callable[[], Optional[SystemSample]]] = None,
    max_gpu_retries: int = 3,
    key_reader: Optional[Callable[[], str]] = None,
    out: Optional[TextIO] = None,
) -> str:
    stream = out if out is not None else sys.stdout
    current = sample
    tries = 0
    while True:
        block = format_gpu_check(current)
        stream.write(block + "\n\n")
        if hasattr(stream, "flush"):
            stream.flush()
        if auto:
            ok = True
        else:
            ok = confirm_yes_no("Is this your GPU?", default_yes=True, key_reader=key_reader, out=stream)
        if ok:
            break
        tries += 1
        if tries >= max_gpu_retries:
            return "quit"
        stream.write("Scanning GPUs again...\n")
        if hasattr(stream, "flush"):
            stream.flush()
        if rescan is not None:
            nxt = rescan()
            if nxt is not None:
                current = nxt
    if not auto:
        if not confirm_yes_no("Proceed boosting?", default_yes=True, key_reader=key_reader, out=stream):
            return "quit"
    stream.write("\n" + format_recovery_manual(current) + "\n\n")
    if hasattr(stream, "flush"):
        stream.flush()
    return "boost"

def log(msg: str) -> None:
    if os.environ.get("HYBRID_QUIET") == "1":
        return
    end_live_status()
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def heartbeat_path() -> Path:
    env = os.environ.get("HYBRIDCOVER_HEARTBEAT")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "overnight-gpu.heartbeat"

def write_heartbeat(round_n: int = 0, extra: str = "") -> None:
    path = heartbeat_path()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    line = (
        f"{stamp} pid={os.getpid()} rev={POLICY_REVISION} "
        f"cat={len(GPU_NAME_CATALOG)} round={round_n}"
    )
    if extra:
        line += f" {extra}"
    try:
        path.write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

def fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "?"
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(x) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(x)}{unit}"
            return f"{x:.1f}{unit}"
        x /= 1024.0
    return f"{n}B"

def fmt_opt(v: Optional[float], suffix: str = "", digits: int = 0) -> str:
    if v is None:
        return "?"
    if digits <= 0:
        return f"{int(round(v))}{suffix}"
    return f"{v:.{digits}f}{suffix}"

def classify_vendor(name: str, vendor_id: int = 0, pnp: str = "") -> str:
    blob = f"{name} {pnp}".lower()
    vid = vendor_id
    if not vid:
        m = re.search(r"ven_([0-9a-f]{4})", blob)
        if m:
            vid = int(m.group(1), 16)
        elif "0x10de" in blob or "pci:10de" in blob:
            vid = NVIDIA_VENDOR_ID
        elif "0x1002" in blob or "0x1022" in blob:
            vid = AMD_VENDOR_ID
        elif "0x8086" in blob:
            vid = INTEL_VENDOR_ID
        elif "0x106b" in blob:
            vid = APPLE_VENDOR_ID
        elif "0x17cb" in blob or "0x5143" in blob:
            vid = QUALCOMM_VENDOR_ID
        elif "0x13b5" in blob:
            vid = ARM_VENDOR_ID
        elif "0x1010" in blob:
            vid = IMG_VENDOR_ID
        elif "0x14e4" in blob:
            vid = BROADCOM_VENDOR_ID
        elif "0x1ed5" in blob:
            vid = MOORETHREADS_VENDOR_ID
        elif "0x144d" in blob:
            vid = SAMSUNG_VENDOR_ID
        elif "0x19e5" in blob:
            vid = HUAWEI_VENDOR_ID
    if vid == NVIDIA_VENDOR_ID or any(k in blob for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "nouveau")):
        return "nvidia"
    if vid in (AMD_VENDOR_ID, 0x1022) or any(k in blob for k in ("amd", "radeon", "780m", "890m", "rx ", "amdgpu")):
        return "amd"
    if vid == INTEL_VENDOR_ID or any(k in blob for k in ("intel", "uhd", "iris", "arc ", "i915", "psb", "i965")):
        return "intel"
    if vid == APPLE_VENDOR_ID or any(k in blob for k in ("apple m1", "apple m2", "apple m3", "apple m4", "apple m5", "apple gpu", "asahi", "apple-agx")):
        return "apple"
    if vid in (QUALCOMM_VENDOR_ID, QUALCOMM_LEGACY_ID) or any(k in blob for k in ("adreno", "qualcomm", "snapdragon", "msm", "kgsl")):
        return "qualcomm"
    if vid == ARM_VENDOR_ID or any(k in blob for k in ("mali", "immortalis", "panfrost", "panthor", "lima")):
        return "arm"
    if vid == IMG_VENDOR_ID or any(k in blob for k in ("powervr", "imagination", "pvrsrvkm")):
        return "img"
    if vid == BROADCOM_VENDOR_ID or any(k in blob for k in ("videocore", "broadcom", "bcm271", "v3d", "vc4")):
        return "broadcom"
    if vid == MOORETHREADS_VENDOR_ID or any(k in blob for k in ("moore threads", "mtt s", "mtgpu")):
        return "moorethreads"
    if vid == SAMSUNG_VENDOR_ID or any(k in blob for k in ("xclipse", "exynos")):
        return "samsung"
    if vid == HUAWEI_VENDOR_ID or any(k in blob for k in ("maleoon", "kirin")):
        return "huawei"
    if any(k in blob for k in ("vivante", "verisilicon", "etnaviv")):
        return "vivante"
    return "other"

def looks_skip_adapter(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in SKIP_ADAPTER_HINTS)

def looks_integrated(name: str, vram_mb: Optional[float] = None) -> bool:
    low = name.lower()
    apu_carveout = (
        "780m",
        "760m",
        "680m",
        "890m",
        "880m",
        "860m",
        "740m",
        "660m",
        "610m",
        "840m",
        "830m",
        "8050s",
        "8060s",
        "8040s",
    )
    amd_apu = ("radeon" in low or "amd" in low) and any(h in low for h in apu_carveout)
    if any(h in low for h in DISCRETE_HINTS) and not amd_apu:
        return False
    if any(h in low for h in INTEGRATED_HINTS):
        return True
    if vram_mb is not None and vram_mb > 0 and vram_mb < 2048:
        return True
    return False

def vendor_id_of(vendor: str, explicit: int = 0) -> int:
    if explicit:
        return explicit
    return VENDOR_IDS.get(vendor, 0)

def discrete_score(gpu: GpuSample) -> float:
    if not gpu.present:
        return -1.0
    integrated = looks_integrated(gpu.name, gpu.vram_total_mb)
    if gpu.discrete is None:
        gpu.discrete = not integrated
    score = 50.0
    if gpu.discrete:
        score += 100.0
    else:
        score -= 20.0
    vram = gpu.vram_total_mb or 0.0
    score += min(vram / 256.0, 40.0)
    return score

def empty_gpu(role_name: str, vendor: str = "other") -> GpuSample:
    return GpuSample(name=f"{role_name} (absent)", vendor=vendor, present=False)

def assign_roles(gpus: Sequence[GpuSample]) -> Tuple[GpuSample, GpuSample, str]:
    live = [g for g in gpus if g.present and not looks_skip_adapter(g.name)]
    if not live:
        return empty_gpu("primary GPU"), empty_gpu("secondary GPU"), "none"
    ranked = sorted(live, key=discrete_score, reverse=True)
    if len(ranked) == 1:
        only = ranked[0]
        if only.discrete is False or looks_integrated(only.name, only.vram_total_mb):
            return empty_gpu("primary GPU", only.vendor), only, "amd_igpu"
        return only, empty_gpu("secondary GPU"), "amd_igpu"
    primary, secondary = ranked[0], ranked[1]
    return primary, secondary, "hybrid"

def solo_gpu(sample: SystemSample) -> GpuSample:
    if sample.igpu.present and not sample.rtx.present:
        return sample.igpu
    if sample.rtx.present and not sample.igpu.present:
        return sample.rtx
    if sample.igpu.present:
        return sample.igpu
    return sample.rtx

@dataclass
class GpuSample:
    name: str
    vendor: str
    util_pct: Optional[float] = None
    clock_mhz: Optional[float] = None
    max_clock_mhz: Optional[float] = None
    power_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    temp_c: Optional[float] = None
    vram_used_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None
    present: bool = True
    vendor_id: int = 0
    discrete: Optional[bool] = None

@dataclass
class MemorySample:
    total_bytes: int
    avail_bytes: int
    commit_used_bytes: Optional[int] = None
    commit_limit_bytes: Optional[int] = None
    memory_load_pct: Optional[float] = None

    @property
    def avail_ratio(self) -> float:
        if self.total_bytes <= 0:
            return 1.0
        return self.avail_bytes / float(self.total_bytes)

@dataclass
class SystemSample:
    ts: float
    rtx: GpuSample
    igpu: GpuSample
    memory: MemorySample
    assist_rss_bytes: int = 0
    assist_alive: bool = False
    assist_tdr_or_fail: bool = False
    ram_avail_at_assist_start: Optional[int] = None
    simulated: bool = False
    source: str = "live"
    profile: str = "auto"
    power_source: str = "unknown"
    hot_procs: List["ProcInfo"] = field(default_factory=list)
    adapters: List["GpuSample"] = field(default_factory=list)

@dataclass
class PauseState:
    until: float = 0.0
    reason: str = ""

    def remaining(self, now: float) -> float:
        return max(0.0, self.until - now)

    def active(self, now: float) -> bool:
        return now < self.until

@dataclass
class ProcInfo:
    pid: int
    name: str
    cpu_pct: float = 0.0
    gpu_pct: float = 0.0
    gpu_mem_mb: float = 0.0
    kind: str = "other"
    exe_path: str = ""

@dataclass
class Workload:
    kind: str
    top_cpu: Optional[ProcInfo] = None
    top_gpu: Optional[ProcInfo] = None
    target: Optional[ProcInfo] = None
    reason: str = ""

@dataclass
class PolicyDecision:
    mode: str
    reason: str
    igpu_assist: bool
    assist_level: str
    budget_bytes: int
    pause_seconds_left: float = 0.0
    safety_reasons: List[str] = field(default_factory=list)
    rtx_weak: bool = False
    rtx_weak_reason: str = "healthy"
    extra_bytes_est: int = 0
    width: int = 0
    height: int = 0
    ring: int = 0
    amd_perf: str = "auto"
    profile: str = "hybrid"
    rtx_boost: bool = False
    gpu_pref: Optional[int] = None
    workload_kind: str = "unknown"
    target_exe: str = ""
    target_name: str = ""
    copies: int = 0
    sleep_ms: int = 0
    on_primary: bool = True
    relaunch_game: bool = False
    assist_step: str = "off"

def estimate_hybrid_extra_bytes(sample: SystemSample, budget_bytes: int) -> int:
    igpu_fb = int((sample.igpu.vram_used_mb or 128.0) * 1024 * 1024)
    fb_growth = min(max(igpu_fb // 4, 64 * 1024 * 1024), 256 * 1024 * 1024)
    assist = max(0, int(budget_bytes))
    optimus = max(96 * 1024 * 1024, assist // 2)
    return int(fb_growth + assist + optimus)

def compute_budget_bytes(sample: SystemSample, mode: str) -> int:
    if mode == "PAUSE":
        return 0
    total = max(0, sample.memory.total_bytes)
    cap = min(MAX_ASSIST_BYTES, int(total * MAX_ASSIST_RAM_RATIO))
    if cap <= 0:
        return 0
    headroom = max(0, sample.memory.avail_bytes - MIN_RAM_AVAIL_BYTES)
    cap = min(cap, headroom)
    cap = min(cap, int(sample.memory.avail_bytes * 0.25))
    if cap < 16 * 1024 * 1024:
        return 0
    budget = cap
    if mode == "BOOST":
        budget = min(budget, 192 * 1024 * 1024, max(48 * 1024 * 1024, cap // 4))
    if sample.power_source == "battery" and mode != "BACKUP":
        budget = int(budget * 0.75)
        if budget < 16 * 1024 * 1024:
            return 0
    return budget

def assist_geometry(mode: str, budget_bytes: int) -> Tuple[int, int, int]:
    if mode == "PAUSE" or budget_bytes <= 0:
        return (0, 0, 0)
    if mode == "BACKUP":
        sizes = ((1920, 1080), (1280, 720), (960, 540), (640, 360))
        max_ring = 8
    else:
        sizes = ((960, 540), (640, 360))
        max_ring = 4
    bpp = 4
    for w, h in sizes:
        frame = w * h * bpp
        per = frame * 2
        ring = max(2, min(max_ring, budget_bytes // per))
        while ring >= 2 and per * ring > budget_bytes:
            ring -= 1
        if ring >= 2:
            return (w, h, int(ring))
    return (0, 0, 0)

def assist_copies(sample: SystemSample, work: Workload, *, mode: str = "BOOST") -> int:
    igpu_u = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    temp = sample.igpu.temp_c
    cap = 8 if mode == "BACKUP" else 4
    if igpu_u >= IGPU_EMERGENCY_UTIL or (temp is not None and temp >= 82.0):
        return ASSIST_MIN_COPIES
    if igpu_u >= IGPU_HOG_UTIL or (temp is not None and temp >= 78.0):
        return 2
    if igpu_u >= TARGET_IGPU_MAX:
        return 4
    if igpu_u >= TARGET_IGPU_UTIL:
        return 6
    gap = TARGET_IGPU_UTIL - igpu_u
    copies = 6 + int(gap / 8.0) * 2
    if work.kind not in {"game", "render"}:
        copies = max(3, copies - 2)
    return int(max(3, min(cap, copies)))

def assist_sleep_ms(sample: SystemSample) -> int:
    igpu_u = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    temp = sample.igpu.temp_c
    error = igpu_u - TARGET_IGPU_UTIL
    sleep = int(round(max(0.0, 1.0 + error * 0.35)))
    if temp is not None and temp >= 78.0:
        sleep = max(sleep, 8)
    if igpu_u >= IGPU_EMERGENCY_UTIL:
        sleep = max(sleep, 18)
    if igpu_u >= IGPU_HOG_UTIL:
        sleep = max(sleep, 10)
    if igpu_u >= TARGET_IGPU_MAX:
        sleep = max(sleep, 4)
    return int(max(0, min(32, sleep)))

def ram_climb_ok(sample: SystemSample, extra_bytes: int = 0) -> bool:
    mem = sample.memory
    leftover = mem.avail_bytes - max(0, int(extra_bytes))
    if leftover < RAM_CLIMB_AVAIL_BYTES:
        return False
    if mem.avail_bytes < RAM_CLIMB_AVAIL_BYTES:
        return False
    if mem.total_bytes > 0:
        if mem.avail_ratio < RAM_CLIMB_AVAIL_RATIO:
            return False
        if leftover / float(mem.total_bytes) < RAM_CLIMB_AVAIL_RATIO:
            return False
    if (
        mem.commit_used_bytes is not None
        and mem.commit_limit_bytes
        and mem.commit_limit_bytes > 0
        and mem.commit_used_bytes / float(mem.commit_limit_bytes) >= COMMIT_PRESSURE_RATIO
    ):
        return False
    return True

def ram_ease_needed(sample: SystemSample) -> bool:
    mem = sample.memory
    if mem.avail_bytes < RAM_EASE_AVAIL_BYTES:
        return True
    if mem.total_bytes > 0 and mem.avail_ratio < RAM_EASE_AVAIL_RATIO:
        return True
    if (
        mem.commit_used_bytes is not None
        and mem.commit_limit_bytes
        and mem.commit_limit_bytes > 0
        and mem.commit_used_bytes / float(mem.commit_limit_bytes) >= COMMIT_PRESSURE_RATIO
    ):
        return True
    return False

def package_strain(sample: SystemSample) -> bool:
    igpu = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    rtx = float(sample.rtx.util_pct) if sample.rtx.util_pct is not None else 0.0
    if igpu < TARGET_IGPU_MAX:
        return False
    if rtx >= TARGET_RTX_MIN:
        return False
    return game_on_nvidia(sample)

def igpu_ease_needed(sample: SystemSample) -> bool:
    igpu = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    temp = sample.igpu.temp_c
    if igpu >= IGPU_HOG_UTIL:
        return True
    if temp is not None and temp >= 78.0:
        return True
    if ram_ease_needed(sample):
        return True
    if package_strain(sample):
        return True
    return False

def step_assist_copies(current: int, target: int) -> int:
    current = max(0, int(current))
    target = max(0, int(target))
    if target > current:
        return min(target, current + ASSIST_CLIMB_STEP)
    if target < current:
        return max(target, current - ASSIST_DROP_STEP)
    return current

def game_nvidia_sm_pct(sample: SystemSample) -> float:
    games = [p for p in (sample.hot_procs or []) if p.kind in {"game", "render"}]
    if not games:
        return 0.0
    return max(float(p.gpu_pct or 0.0) for p in games)

def game_misplaced_on_igpu(sample: SystemSample) -> bool:
    igpu = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    rtx = float(sample.rtx.util_pct) if sample.rtx.util_pct is not None else 0.0
    if game_nvidia_sm_pct(sample) >= GAME_ON_NVIDIA_SM_PCT:
        return False
    if igpu < MISPLACED_IGPU_UTIL:
        return False
    if rtx >= MISPLACED_RTX_UTIL:
        return False
    games = [p for p in (sample.hot_procs or []) if p.kind in {"game", "render"}]
    if games:
        return True
    return igpu >= 80.0

def game_on_nvidia(sample: SystemSample) -> bool:
    if game_misplaced_on_igpu(sample):
        return False
    games = [p for p in (sample.hot_procs or []) if p.kind in {"game", "render"}]
    if game_nvidia_sm_pct(sample) >= GAME_ON_NVIDIA_SM_PCT:
        return True
    vram = float(sample.rtx.vram_used_mb or 0.0)
    igpu = float(sample.igpu.util_pct) if sample.igpu.util_pct is not None else 0.0
    if games and vram >= RTX_GAME_VRAM_MB and igpu < MISPLACED_IGPU_UTIL:
        return True
    if not games:
        return (sample.rtx.util_pct or 0.0) >= TARGET_RTX_MIN
    return False

def rtx_ready_for_igpu(sample: SystemSample) -> bool:
    if not game_on_nvidia(sample):
        return False
    rtx = float(sample.rtx.util_pct) if sample.rtx.util_pct is not None else 0.0
    return rtx >= TARGET_RTX_MIN

def is_roblox_name(name: str, exe_path: str = "") -> bool:
    return "roblox" in f"{name} {exe_path}".lower()

def is_roblox_workload(sample: SystemSample, work: Optional[Workload] = None) -> bool:
    if work and work.target and is_roblox_name(work.target.name, work.target.exe_path):
        return True
    return any(is_roblox_name(p.name, p.exe_path) for p in (sample.hot_procs or []))

def _blob_name_path(name: str, exe_path: str = "") -> str:
    return f"{name} {exe_path}".replace("\\", "/").lower()

def is_relaunch_skip_name(name: str, exe_path: str = "") -> bool:
    blob = _blob_name_path(name, exe_path)
    return any(tok in blob for tok in RELAUNCH_SKIP_TOKENS)

def gpu_heavy_family(name: str, exe_path: str = "") -> str:
    blob = _blob_name_path(name, exe_path)
    if "roblox" in blob:
        return "roblox"
    if "valorant" in blob or "riot" in blob:
        return "valorant"
    if "webots" in blob or "cyberbotics" in blob:
        return "webots"
    if "coppelia" in blob:
        return "coppelia"
    if "unity" in blob:
        return "unity"
    if "unreal" in blob or "ue4" in blob or "ue5" in blob:
        return "unreal"
    base = _exe_basename(name or exe_path).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base

def is_gpu_heavy_name(name: str, exe_path: str = "") -> bool:
    if is_relaunch_skip_name(name, exe_path):
        return False
    blob = _blob_name_path(name, exe_path)
    if any(tok in blob for tok in GPU_HEAVY_NAME_TOKENS):
        return True
    base = _exe_basename(name or exe_path).lower()
    pins = {p.lower() for p in GPU_HEAVY_PIN_BASENAMES}
    if base in pins or (base.endswith(".exe") is False and (base + ".exe") in pins):
        return True
    return classify_proc_kind(name, exe_path) in {"game", "render"}

def is_gpu_heavy_workload(sample: SystemSample, work: Optional[Workload] = None) -> bool:
    if work and work.target and is_gpu_heavy_name(work.target.name, work.target.exe_path):
        return True
    return any(is_gpu_heavy_name(p.name, p.exe_path) for p in (sample.hot_procs or []) if p.kind in {"game", "render"})

def can_relaunch_misplaced(sample: SystemSample, work: Optional[Workload] = None) -> bool:
    if work is not None and work.kind == "render":
        return False
    if work is not None and work.target:
        if classify_proc_kind(work.target.name, work.target.exe_path) == "render":
            return False
        return is_gpu_heavy_name(work.target.name, work.target.exe_path)
    return any(
        p.kind == "game" and is_gpu_heavy_name(p.name, p.exe_path)
        for p in (sample.hot_procs or [])
    )

def _exe_basename(path: str) -> str:
    text = (path or "").replace("\\", "/").strip()
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]

def _pinable_gpu_pref_name(path: str) -> bool:
    base = _exe_basename(path).lower()
    if not base:
        return False
    if "crashhandler" in base or "crashpad" in base:
        return False
    return True

def _unique_paths(paths: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for path in paths:
        text = (path or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out

def _roblox_version_roots() -> List[Path]:
    roots: List[Path] = []
    for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(key) or ""
        if base:
            roots.append(Path(base) / "Roblox" / "Versions")
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if home:
        roots.append(Path(home) / "AppData" / "Local" / "Roblox" / "Versions")
    return roots

def _gpu_heavy_search_roots() -> List[Path]:
    roots: List[Path] = list(_roblox_version_roots())
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "USERPROFILE"):
        base = os.environ.get(key) or ""
        if not base:
            continue
        path = Path(base)
        roots.extend(
            [
                path / "Webots",
                path / "Cyberbotics" / "Webots",
                path / "Riot Games",
                path / "VALORANT",
                path / "Roblox" / "Versions",
            ]
        )
    roots.append(Path(r"C:\Riot Games"))
    roots.append(Path(r"C:\Program Files\Webots"))
    roots.append(Path(r"C:\Program Files\Cyberbotics\Webots"))
    out: List[Path] = []
    seen = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out

def discover_pin_exes(sample: SystemSample) -> List[str]:
    found: List[str] = list(GPU_HEAVY_PIN_BASENAMES)
    for proc in sample.hot_procs or []:
        if proc.kind in {"game", "render"} or is_gpu_heavy_name(proc.name, proc.exe_path):
            found.append(_target_exe(proc))
            name = (proc.name or "").strip()
            if name:
                found.append(name if name.lower().endswith(".exe") else name + ".exe")
    patterns = (
        "*/RobloxPlayerBeta.exe",
        "*/RobloxPlayer.exe",
        "*/RobloxPlayerLauncher.exe",
        "*/RobloxStudioBeta.exe",
        "**/webots.exe",
        "**/webots-bin.exe",
        "**/VALORANT-Win64-Shipping.exe",
        "**/VALORANT.exe",
        "**/coppeliaSim.exe",
    )
    for root in _gpu_heavy_search_roots():
        if not root.is_dir():
            continue
        for pattern in patterns:
            try:
                for exe in root.glob(pattern):
                    found.append(str(exe))
            except OSError:
                continue
    found = _unique_paths(found)
    return [p for p in found if _pinable_gpu_pref_name(p)]

_LIVE_PIN_CACHE: Tuple[float, List[str]] = (0.0, [])

def _gpu_heavy_live_regex() -> str:
    return (
        "Roblox|Valorant|Webots|coppelia|Gazebo|Unity|Unreal|"
        "Genshin|StarRail|Fortnite|League|Cyberpunk|eldenring|"
        "Palworld|VRChat|blender|Maya|3dsmax"
    )

def discover_live_pin_targets() -> List[str]:
    global _LIVE_PIN_CACHE
    now = time.time()
    ts, cached = _LIVE_PIN_CACHE
    if now - ts < 5.0 and cached:
        return cached
    if os.name != "nt":
        return []
    pattern = _gpu_heavy_live_regex()
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$pat = '{pattern}'
$out = New-Object System.Collections.Generic.List[string]
Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -match $pat -and $_.Name -notmatch 'CrashHandler|crashpad|EasyAntiCheat|BattlEye|vgtray'
}} | ForEach-Object {{
  if ($_.ExecutablePath) {{ [void]$out.Add([string]$_.ExecutablePath) }}
}}
try {{
  Get-StartApps | Where-Object {{ $_.Name -match $pat }} | ForEach-Object {{
    if ($_.AppID) {{ [void]$out.Add([string]$_.AppID) }}
  }}
}} catch {{}}
try {{
  Get-AppxPackage | Where-Object {{ $_.Name -match $pat }} | ForEach-Object {{
    if ($_.PackageFamilyName) {{ [void]$out.Add([string]($_.PackageFamilyName + '!App')) }}
  }}
}} catch {{}}
$out | Select-Object -Unique | ConvertTo-Json -Compress
"""
    data = _ps_json(script, timeout=6.0)
    found: List[str] = []
    if isinstance(data, str):
        found = [data]
    elif isinstance(data, list):
        found = [str(x) for x in data if x]
    found = _unique_paths([p for p in found if _pinable_gpu_pref_name(p)])
    _LIVE_PIN_CACHE = (now, found)
    return found

def preference_pin_names(sample: SystemSample, decision: PolicyDecision) -> List[str]:
    if decision.gpu_pref == 1:
        found: List[str] = [decision.target_exe]
        found.extend(remainder_pin_names(sample))
        return _unique_paths(
            [
                p
                for p in found
                if _pinable_gpu_pref_name(p) and not is_gpu_heavy_name(p, p)
            ]
        )
    found = [decision.target_exe]
    found.extend(discover_pin_exes(sample))
    found.extend(discover_live_pin_targets())
    with_base: List[str] = []
    for path in found:
        with_base.append(path)
        base = _exe_basename(path)
        if base:
            with_base.append(base)
    return _unique_paths([p for p in with_base if _pinable_gpu_pref_name(p)])

def remainder_pin_names(sample: SystemSample) -> List[str]:
    found: List[str] = list(IGPU_REMAINDER_BASENAMES)
    for proc in sample.hot_procs or []:
        if proc.kind in {"game", "render", "system"}:
            continue
        if is_gpu_heavy_name(proc.name, proc.exe_path):
            continue
        found.append(_target_exe(proc))
        name = (proc.name or "").strip()
        if name:
            found.append(name if name.lower().endswith(".exe") else name + ".exe")
    return _unique_paths([p for p in found if _pinable_gpu_pref_name(p)])

def split_win_command_line(command_line: str) -> List[str]:
    text = (command_line or "").strip()
    if not text:
        return []
    out: List[str] = []
    buf: List[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch in {" ", "\t"} and not in_quote:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out

def pick_heavy_relaunch_spec(
    rows: Sequence[Dict[str, Any]],
    *,
    prefer: str = "",
) -> Optional[Dict[str, Any]]:
    prefer_fam = gpu_heavy_family(prefer, prefer) if prefer else ""
    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows:
        name = str(row.get("name") or row.get("Name") or "")
        exe = str(row.get("exe") or row.get("ExecutablePath") or row.get("exe_path") or "")
        blob = _blob_name_path(name, exe)
        if is_relaunch_skip_name(name, exe):
            continue
        if not (
            is_gpu_heavy_name(name, exe)
            or "roblox" in blob
            or "valorant" in blob
            or "webots" in blob
        ):
            continue
        score = 1
        if "shipping" in blob or "playerbeta" in blob:
            score = 6
        elif "webots.exe" in blob and "bin" not in _exe_basename(name or exe).lower():
            score = 8
        elif "webots-bin" in blob:
            score = 3
        elif "webots" in blob:
            score = 4
        elif "valorant" in blob:
            score = 5
        elif "roblox" in blob and "launcher" not in blob and "studio" not in blob:
            score = 5
        elif "launcher" in blob or "riotclient" in blob:
            score = 1
        elif "studio" in blob:
            score = 2
        if row.get("command_line") or row.get("CommandLine"):
            score += 2
        if row.get("exe") or row.get("ExecutablePath") or row.get("exe_path"):
            score += 1
        if prefer_fam and gpu_heavy_family(name, exe) == prefer_fam:
            score += 5
        ranked.append((score, row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]

def pick_roblox_relaunch_spec(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    roblox_rows = [
        row
        for row in rows
        if "roblox" in str(row.get("name") or row.get("Name") or "").lower()
    ]
    return pick_heavy_relaunch_spec(roblox_rows, prefer="roblox")

def normalize_launch_spec(row: Dict[str, Any]) -> Dict[str, Any]:
    exe = str(row.get("exe") or row.get("ExecutablePath") or row.get("exe_path") or "")
    command = str(row.get("command_line") or row.get("CommandLine") or "")
    name = str(row.get("name") or row.get("Name") or "")
    pid = int(row.get("pid") or row.get("ProcessId") or 0)
    parts = split_win_command_line(command)
    if not exe and parts:
        exe = parts[0]
    args = parts[1:] if parts else []
    return {"pid": pid, "name": name, "exe": exe, "command_line": command, "args": args}

def list_gpu_heavy_processes() -> List[Dict[str, Any]]:
    if os.name != "nt":
        return []
    pattern = _gpu_heavy_live_regex()
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$pat = '{pattern}'
Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -match $pat -and $_.Name -notmatch 'CrashHandler|crashpad|EasyAntiCheat|BattlEye|vgtray'
}} | ForEach-Object {{
  [pscustomobject]@{{
    name = $_.Name
    pid = $_.ProcessId
    exe = $_.ExecutablePath
    command_line = $_.CommandLine
  }}
}} | ConvertTo-Json -Compress
"""
    data = _ps_json(script, timeout=8.0)
    rows: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [x for x in data if isinstance(x, dict)]
    return [normalize_launch_spec(row) for row in rows]

def list_roblox_processes() -> List[Dict[str, Any]]:
    return [
        row
        for row in list_gpu_heavy_processes()
        if "roblox" in str(row.get("name") or "").lower()
    ]

def relaunch_disabled() -> bool:
    return os.environ.get("HYBRID_NO_RELAUNCH", "").strip().lower() in {"1", "true", "yes", "on"}

def should_relaunch_misplaced(
    decision: PolicyDecision,
    *,
    streak: int,
    last_relaunch: float,
    now: float,
) -> bool:
    if not decision.relaunch_game:
        return False
    if relaunch_disabled():
        return False
    if streak < RELAUNCH_CONFIRM_CYCLES:
        return False
    if last_relaunch and (now - last_relaunch) < RELAUNCH_COOLDOWN_SEC:
        return False
    return True

def relaunch_heavy_onto_dgpu(
    *,
    dry_run: bool = False,
    specs: Optional[Sequence[Dict[str, Any]]] = None,
    prefer: str = "",
) -> List[str]:
    if dry_run:
        return ["[dry-run] kill GPU-heavy process and relaunch on RTX"]
    if os.name != "nt":
        return []
    rows = list(specs) if specs is not None else list_gpu_heavy_processes()
    normalized = [normalize_launch_spec(row) if "args" not in row else dict(row) for row in rows]
    target = pick_heavy_relaunch_spec(normalized, prefer=prefer)
    if target is None:
        return ["warning: no GPU-heavy process to relaunch"]
    spec = normalize_launch_spec(target)
    exe = spec.get("exe") or ""
    args = list(spec.get("args") or [])
    if not exe:
        return ["warning: could not read GPU-heavy process path"]
    family = gpu_heavy_family(str(spec.get("name") or ""), exe)
    notes: List[str] = []
    pids: List[int] = []
    for row in normalized:
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            continue
        row_fam = gpu_heavy_family(str(row.get("name") or ""), str(row.get("exe") or ""))
        if row_fam != family:
            continue
        if is_relaunch_skip_name(str(row.get("name") or ""), str(row.get("exe") or "")):
            continue
        if family == "valorant" and "riotclient" in _blob_name_path(
            str(row.get("name") or ""), str(row.get("exe") or "")
        ):
            continue
        pids.append(pid)
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            notes.append(f"warning: pid={pid} kill failed ({exc})")
    time.sleep(1.2)
    cwd = str(Path(exe).parent) if exe else None
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags |= 0x01000000
    try:
        subprocess.Popen(
            [exe, *args],
            cwd=cwd or None,
            close_fds=True,
            creationflags=flags if os.name == "nt" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        notes.append(f"warning: relaunch {Path(exe).name} failed ({exc})")
        return notes
    notes.append(f"relaunched {Path(exe).name} onto RTX ({family})")
    return notes

def relaunch_roblox_onto_dgpu(
    *,
    dry_run: bool = False,
    specs: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    return relaunch_heavy_onto_dgpu(dry_run=dry_run, specs=specs, prefer="roblox")

def nvidia_clock_range(
    max_clk: Optional[float],
    floor_ratio: float = NVIDIA_CLOCK_FLOOR_RATIO,
) -> Tuple[int, int]:
    if not max_clk or max_clk < 1000:
        return (0, 0)
    return (int(max_clk * floor_ratio), int(max_clk))

def rtx_needs_unlock(sample: SystemSample) -> bool:
    util = sample.rtx.util_pct
    if util is None:
        return True
    return util < RTX_STARVED_UTIL

def should_retry_nvidia_unlock(
    guard: "RegistryGuard",
    sample: SystemSample,
    now: Optional[float] = None,
) -> bool:
    if not guard.nvidia_unlock_done:
        return True
    if not rtx_needs_unlock(sample):
        return False
    now = time.time() if now is None else now
    return (now - float(guard.nvidia_unlock_last_try or 0.0)) >= NVIDIA_UNLOCK_RETRY_SEC

def rtx_is_weak(
    rtx: GpuSample,
    history: Sequence[GpuSample] | None = None,
    peak_power_limit_w: Optional[float] = None,
    peak_util: Optional[float] = None,
) -> Tuple[bool, str]:
    if not rtx.present:
        return True, "rtx_missing"

    util = float(rtx.util_pct) if rtx.util_pct is not None else None
    clock = rtx.clock_mhz
    max_clock = rtx.max_clock_mhz
    power = rtx.power_w
    pl = rtx.power_limit_w

    if util is not None and util >= RTX_HIGH_UTIL and clock and max_clock:
        if max_clock > 0 and clock < max_clock * RTX_CLOCK_DROP_RATIO:
            return True, "high_util_clock_drop"

    tgp = peak_power_limit_w
    if pl and tgp and pl < tgp * RTX_POWER_LIMIT_DROP_RATIO:
        if util is None or util >= 55.0:
            return True, "power_limit_below_tgp"
    if (
        util is not None
        and util >= 60.0
        and power is not None
        and pl is not None
        and pl > 0
        and power >= pl * 0.95
        and clock
        and max_clock
        and max_clock > 0
        and clock < max_clock * RTX_CLOCK_DROP_RATIO
    ):
        return True, "power_throttled_clock_drop"

    hist_peak = peak_util
    if history:
        vals = [h.util_pct for h in history if h.util_pct is not None]
        if vals:
            hist_peak = max(hist_peak or 0.0, max(vals))
    if (
        util is not None
        and hist_peak is not None
        and hist_peak >= RTX_HIGH_UTIL
        and util <= hist_peak - SUDDEN_UTIL_DROP_POINTS
        and util < 50.0
    ):
        return True, "sudden_util_drop"

    return False, "healthy"

def collect_safety_reasons(
    sample: SystemSample,
    budget_bytes: int,
) -> Tuple[List[str], int]:
    reasons: List[str] = []
    mem = sample.memory
    extra = estimate_hybrid_extra_bytes(sample, budget_bytes)

    if mem.avail_bytes < MIN_RAM_AVAIL_BYTES:
        reasons.append("ram_below_1_5gb")
    if mem.total_bytes > 0 and mem.avail_ratio < MIN_RAM_AVAIL_RATIO:
        reasons.append("ram_below_8pct")

    leftover = mem.avail_bytes - extra
    leftover_ratio = leftover / float(mem.total_bytes) if mem.total_bytes else 1.0
    if leftover < MIN_LEFTOVER_AFTER_HYBRID or leftover_ratio < MIN_RAM_AVAIL_RATIO:
        reasons.append("hybrid_extra_would_starve_ram")

    if (
        mem.commit_used_bytes is not None
        and mem.commit_limit_bytes is not None
        and mem.commit_limit_bytes > 0
    ):
        commit_ratio = mem.commit_used_bytes / float(mem.commit_limit_bytes)
        commit_avail = mem.commit_limit_bytes - mem.commit_used_bytes
        if commit_ratio >= COMMIT_PRESSURE_RATIO:
            reasons.append("commit_pressure")
        if commit_avail < extra + 512 * 1024 * 1024:
            reasons.append("commit_headroom_low")

    if sample.assist_alive and sample.ram_avail_at_assist_start is not None:
        start = sample.ram_avail_at_assist_start
        drop = start - mem.avail_bytes
        remaining_tight = mem.avail_bytes < RAM_COLLAPSE_FLOOR_BYTES or (
            mem.total_bytes > 0 and mem.avail_ratio < RAM_COLLAPSE_FLOOR_RATIO
        )
        if remaining_tight:
            if drop >= RAM_COLLAPSE_DROP_BYTES:
                reasons.append("ram_collapsing")
            elif (
                start > 0
                and drop / float(start) >= RAM_COLLAPSE_DROP_RATIO
                and drop > 512 * 1024 * 1024
            ):
                reasons.append("ram_collapsing")

    rtx_t = sample.rtx.temp_c
    igpu_t = sample.igpu.temp_c
    if rtx_t is not None and rtx_t >= HOT_RTX_ALONE_C:
        reasons.append("rtx_too_hot_for_extra")
    if igpu_t is not None and igpu_t >= HOT_IGPU_ALONE_C:
        reasons.append("igpu_too_hot")
    if (
        rtx_t is not None
        and igpu_t is not None
        and rtx_t >= HOT_RTX_C
        and igpu_t >= HOT_IGPU_C
    ):
        reasons.append("both_gpus_too_hot")

    rss_cap = max(int(budget_bytes * 1.25), 400 * 1024 * 1024)
    if sample.assist_rss_bytes > rss_cap:
        reasons.append("assist_rss_too_large")
    if sample.assist_tdr_or_fail:
        reasons.append("gpu_tdr_or_assist_fail")

    return reasons, extra

_SYSTEM_PROC_NAMES = frozenset(
    {
        "idle",
        "system",
        "registry",
        "smss",
        "smss.exe",
        "csrss",
        "csrss.exe",
        "wininit",
        "wininit.exe",
        "services",
        "services.exe",
        "lsass",
        "lsass.exe",
        "svchost",
        "svchost.exe",
        "dwm",
        "dwm.exe",
        "explorer",
        "explorer.exe",
        "fontdrvhost",
        "fontdrvhost.exe",
        "sihost",
        "sihost.exe",
        "taskhostw",
        "taskhostw.exe",
        "searchhost",
        "searchhost.exe",
        "runtimebroker",
        "runtimebroker.exe",
        "startmenuexperiencehost",
        "startmenuexperiencehost.exe",
        "shellexperiencehost",
        "shellexperiencehost.exe",
        "textinputhost",
        "textinputhost.exe",
        "conhost",
        "conhost.exe",
        "dllhost",
        "dllhost.exe",
        "wmiprvse",
        "wmiprvse.exe",
        "wmiapsrv",
        "wmiapsrv.exe",
        "hybrid-gpu-assist",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "python",
        "python.exe",
        "pythonw",
        "pythonw.exe",
        "hybrid_gpu.py",
        "nvidia-smi",
        "nvidia-smi.exe",
        "nvcontainer",
        "nvcontainer.exe",
        "nvdisplay.container",
        "nvdisplay.container.exe",
        "nvxdsync",
        "nvxdsync.exe",
        "amdfendrsr",
        "amdfendrsr.exe",
        "amdow",
        "amdow.exe",
        "radeonsoftware",
        "radeonsoftware.exe",
        "atiesvc",
        "atiesvc.exe",
        "atieclxx",
        "atieclxx.exe",
        "igfxem",
        "igfxem.exe",
        "igfxtray",
        "igfxtray.exe",
        "securityhealthservice",
        "securityhealthservice.exe",
        "msmpeng",
        "msmpeng.exe",
        "audiodg",
        "audiodg.exe",
        "winlogon",
        "winlogon.exe",
        "memory compression",
        "system interrupts",
    }
)
_GAME_TOKENS = (
    "unreal",
    "unityplayer",
    "ue4game",
    "ue5game",
    "valorant",
    "league of legends",
    "leagueoflegends",
    "riotclient",
    "fortnite",
    "battleye",
    "easyanticheat",
    "gta5",
    "gtav",
    "rdr2",
    "cyberpunk",
    "eldenring",
    "witcher3",
    "minecraft",
    "javaw",
    "roblox",
    "robloxplayer",
    "robloxplayerbeta",
    "webots",
    "cyberbotics",
    "coppeliasim",
    "gazebo",
    "isaac-sim",
    "omniverse",
    "overwatch",
    "apexlegends",
    "r5apex",
    "cs2",
    "csgo",
    "dota2",
    "pubg",
    "tslgame",
    "lostark",
    "maplestory",
    "genshin",
    "starrail",
    "zenless",
    "wuwa",
    "wow.exe",
    "wowclassic",
    "hearthstone",
    "diablo",
    "modernwarfare",
    "warzone",
    "cod.exe",
    "rainbowsix",
    "destiny2",
    "warframe",
    "pathofexile",
    "osu!",
    "osu.exe",
    "stardew",
    "terraria",
    "palworld",
    "helldivers",
    "blackdesert",
    "fifa",
    "fc25",
    "fc26",
    "nba2k",
    "rocketleague",
    "deltaforce",
    "once_human",
    "thefinals",
    "huntgame",
    "deadbydaylight",
    "left4dead",
    "tf2",
    "hl2",
    "vrchat",
    "beatsaber",
    "gta",
    "gameoverlay",
)
_RENDER_TOKENS = (
    "blender",
    "maya.bin",
    "maya.exe",
    "3dsmax",
    "cinema 4d",
    "c4d.exe",
    "houdini",
    "nuke",
    "premiere",
    "afterfx",
    "adobe media encoder",
    "resolve",
    "davinci",
    "vegas",
    "obs64",
    "obs32",
    "obs.exe",
    "streamlabs",
    "xsplit",
    "sketchup",
    "solidworks",
    "inventor",
    "acad.exe",
    "revit",
    "rhino",
    "octane",
    "redshift",
    "vray",
    "katana",
    "substance",
    "ffmpeg",
    "handbrake",
    "adobe_media",
    "unrealeditor",
    "ue4editor",
    "ue5editor",
    "unity.exe",
    "zbrush",
    "painter.exe",
    "nvenc",
)
_VIDEO_TOKENS = (
    "vlc",
    "mpc-hc",
    "mpc-be",
    "potplayer",
    "kmplayer",
    "mpv",
    "wmplayer",
    "microsoft.media.player",
    "netflix",
    "disney",
    "primevideo",
    "chrome",
    "msedge",
    "firefox",
    "brave",
    "opera",
    "whale",
    "iexplore",
    "webview2",
    "spotify",
    "youtube",
)

def _norm_proc_name(name: str) -> str:
    text = (name or "").strip().lower()
    if "#" in text:
        text = text.split("#", 1)[0]
    if text.endswith(".exe"):
        stem = text
    else:
        stem = text
    return stem

def classify_proc_kind(name: str, exe_path: str = "") -> str:
    raw = _norm_proc_name(name)
    path = (exe_path or "").replace("\\", "/").lower()
    blob = f"{raw} {path}"
    if raw in _SYSTEM_PROC_NAMES or raw.rstrip(".exe") in _SYSTEM_PROC_NAMES:
        return "system"
    if "hybrid-gpu-assist" in blob or "hybrid_gpu" in blob:
        return "system"
    if any(tok in blob for tok in _RENDER_TOKENS):
        return "render"
    if "steamapps" in path or "/games/" in path or "\\games\\" in (exe_path or "").lower():
        return "game"
    if any(tok in blob for tok in _GAME_TOKENS):
        return "game"
    if any(tok in blob for tok in _VIDEO_TOKENS):
        return "video"
    if raw.endswith("game.exe") or raw.endswith("-win64-shipping.exe"):
        return "game"
    return "other"

def _target_exe(proc: Optional[ProcInfo]) -> str:
    if proc is None:
        return ""
    if proc.exe_path:
        return proc.exe_path
    name = (proc.name or "").strip()
    if name and not name.lower().endswith(".exe") and os.name == "nt":
        return name + ".exe"
    return name

def infer_workload(sample: SystemSample) -> Workload:
    procs = list(sample.hot_procs or [])
    rtx_u = float(sample.rtx.util_pct) if sample.rtx.present and sample.rtx.util_pct is not None else 0.0
    igpu_u = float(sample.igpu.util_pct) if sample.igpu.present and sample.igpu.util_pct is not None else 0.0
    interesting = [p for p in procs if p.kind != "system"]
    top_cpu = max(interesting, key=lambda p: p.cpu_pct, default=None) if interesting else None
    top_gpu = max(interesting, key=lambda p: p.gpu_pct, default=None) if interesting else None
    hot = [p for p in interesting if p.cpu_pct >= HOT_CPU_PCT or p.gpu_pct >= HOT_GPU_PCT]
    idle_gpu = rtx_u < IDLE_RTX_UTIL and igpu_u < IDLE_IGPU_UTIL
    if not hot and idle_gpu:
        return Workload(kind="idle", top_cpu=top_cpu, top_gpu=top_gpu, reason="no_hot_process")

    gpu_heavy = [
        p
        for p in interesting
        if p.kind in {"game", "render"} or is_gpu_heavy_name(p.name, p.exe_path)
    ]
    if gpu_heavy:
        target = max(gpu_heavy, key=lambda p: (p.cpu_pct, p.gpu_pct))
        kind = "render" if target.kind == "render" else "game"
        return Workload(
            kind=kind,
            top_cpu=top_cpu,
            top_gpu=top_gpu,
            target=target,
            reason=f"heavy:{target.name}",
        )

    ranked = sorted(hot or interesting, key=lambda p: (p.gpu_pct, p.cpu_pct), reverse=True)
    target = ranked[0] if ranked else None
    if target is not None:
        kind = target.kind
        if kind == "other":
            if target.gpu_pct >= 20.0 or rtx_u >= 40.0:
                kind = "game"
            elif target.cpu_pct >= 25.0 and target.gpu_pct < 8.0:
                kind = "cpu"
            else:
                kind = "mixed"
        if kind == "video" and (target.gpu_pct >= 40.0 or rtx_u >= 55.0):
            kind = "game"
        if kind == "other" and target.name.lower().startswith("javaw") and target.gpu_pct < 15.0:
            kind = "cpu"
        return Workload(
            kind=kind,
            top_cpu=top_cpu,
            top_gpu=top_gpu,
            target=target,
            reason=f"hot:{target.name}",
        )
    if rtx_u >= 12.0:
        return Workload(kind="game", top_cpu=top_cpu, top_gpu=top_gpu, reason="rtx_util_implies_3d")
    if igpu_u >= 30.0 and rtx_u < 20.0:
        return Workload(kind="video", top_cpu=top_cpu, top_gpu=top_gpu, reason="igpu_decode")
    if rtx_u >= IDLE_RTX_UTIL or igpu_u >= IDLE_IGPU_UTIL:
        return Workload(kind="mixed", top_cpu=top_cpu, top_gpu=top_gpu, reason="gpu_busy_unnamed")
    return Workload(kind="idle", top_cpu=top_cpu, top_gpu=top_gpu, reason="quiet")

def parse_nvidia_pmon_line(line: str) -> Optional[ProcInfo]:
    text = (line or "").strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split()
    if len(parts) < 8:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    if pid <= 0:
        return None

    def _num(idx: int) -> float:
        raw = parts[idx]
        if raw in {"-", "N/A", "[N/A]"}:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    sm = _num(3)
    name = parts[7]
    return ProcInfo(
        pid=pid,
        name=name,
        gpu_pct=sm,
        gpu_mem_mb=_num(4),
        kind=classify_proc_kind(name),
    )

def merge_proc_lists(*groups: Sequence[ProcInfo]) -> List[ProcInfo]:
    by_pid: Dict[int, ProcInfo] = {}
    for group in groups:
        for proc in group:
            if proc.pid <= 0:
                continue
            cur = by_pid.get(proc.pid)
            if cur is None:
                by_pid[proc.pid] = ProcInfo(
                    pid=proc.pid,
                    name=proc.name,
                    cpu_pct=proc.cpu_pct,
                    gpu_pct=proc.gpu_pct,
                    gpu_mem_mb=proc.gpu_mem_mb,
                    kind=classify_proc_kind(proc.name, proc.exe_path),
                    exe_path=proc.exe_path,
                )
                continue
            cur.cpu_pct = max(cur.cpu_pct, proc.cpu_pct)
            cur.gpu_pct = max(cur.gpu_pct, proc.gpu_pct)
            cur.gpu_mem_mb = max(cur.gpu_mem_mb, proc.gpu_mem_mb)
            if proc.exe_path and not cur.exe_path:
                cur.exe_path = proc.exe_path
            if proc.name and (len(proc.name) > len(cur.name) or cur.name.lower() in {"unknown", ""}):
                cur.name = proc.name
            cur.kind = classify_proc_kind(cur.name, cur.exe_path)
    ranked = sorted(by_pid.values(), key=lambda p: (p.gpu_pct, p.cpu_pct), reverse=True)
    return ranked[:12]

def _read_hot_cpu_windows() -> List[ProcInfo]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
  Where-Object { $_.IDProcess -gt 0 -and $_.Name -notmatch '^Idle$|^_Total$' } |
  Sort-Object PercentProcessorTime -Descending |
  Select-Object -First 12 Name, IDProcess, PercentProcessorTime
$out = @()
foreach ($row in $rows) {
  $path = $null
  try { $path = (Get-Process -Id $row.IDProcess -ErrorAction SilentlyContinue).Path } catch {}
  $out += [pscustomobject]@{
    pid = [int]$row.IDProcess
    name = [string]$row.Name
    cpu = [double]$row.PercentProcessorTime
    exe = $path
  }
}
Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match 'Roblox' -and $_.Name -notmatch 'CrashHandler'
} | ForEach-Object {
  $cpu = 0
  $out += [pscustomobject]@{
    pid = [int]$_.ProcessId
    name = [string]$_.Name
    cpu = [double]$cpu
    exe = [string]$_.ExecutablePath
  }
}
$out | ConvertTo-Json -Compress
"""
    data = _ps_json(script, timeout=5.0)
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    out: List[ProcInfo] = []
    if not isinstance(data, list):
        return []
    for row in data:
        try:
            pid = int(row.get("pid") or 0)
            name = str(row.get("name") or "")
            cpu = float(row.get("cpu") or 0.0)
            exe = str(row.get("exe") or "") if row.get("exe") else ""
        except (TypeError, ValueError):
            continue
        if pid <= 0 or not name:
            continue
        out.append(
            ProcInfo(
                pid=pid,
                name=name,
                cpu_pct=cpu,
                kind=classify_proc_kind(name, exe),
                exe_path=exe,
            )
        )
    return out

def _read_hot_cpu_linux() -> List[ProcInfo]:
    ps_bin = shutil.which("ps")
    if not ps_bin:
        return []
    try:
        proc = subprocess.run(
            [ps_bin, "-eo", "pid,pcpu,comm", "--no-headers", "--sort=-pcpu"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []
    out: List[ProcInfo] = []
    for line in proc.stdout.splitlines()[:16]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[1])
        except ValueError:
            continue
        name = parts[2].strip()
        if pid <= 0 or not name:
            continue
        out.append(
            ProcInfo(
                pid=pid,
                name=name,
                cpu_pct=cpu,
                kind=classify_proc_kind(name),
            )
        )
    return out[:12]

def _read_nvidia_pmon() -> List[ProcInfo]:
    smi = _which_nvidia_smi()
    if not smi:
        return []
    try:
        proc = subprocess.run(
            [smi, "pmon", "-c", "1"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout:
        return []
    out: List[ProcInfo] = []
    for line in proc.stdout.splitlines():
        info = parse_nvidia_pmon_line(line)
        if info:
            out.append(info)
    return out

def read_hot_processes() -> List[ProcInfo]:
    cpu = _read_hot_cpu_windows() if os.name == "nt" else _read_hot_cpu_linux()
    gpu = _read_nvidia_pmon()
    return merge_proc_lists(cpu, gpu)

def resolve_profile(sample: SystemSample, force_amd: bool = False) -> str:
    if force_amd or sample.profile in {"amd_igpu", "solo"}:
        return "amd_igpu"
    if sample.profile == "hybrid":
        return "hybrid"
    if sample.igpu.present and sample.rtx.present:
        return "hybrid"
    if sample.igpu.present or sample.rtx.present:
        return "amd_igpu"
    return "hybrid"

def igpu_is_weak(
    igpu: GpuSample,
    history: Sequence[GpuSample] | None = None,
    peak_util: Optional[float] = None,
    power_source: str = "unknown",
) -> Tuple[bool, str]:
    if not igpu.present:
        return True, "igpu_missing"
    if power_source == "battery":
        return True, "battery"
    util = float(igpu.util_pct) if igpu.util_pct is not None else None
    clock = igpu.clock_mhz
    max_clock = igpu.max_clock_mhz
    if util is not None and util >= RTX_HIGH_UTIL and clock and max_clock:
        if max_clock > 0 and clock < max_clock * RTX_CLOCK_DROP_RATIO:
            return True, "high_util_clock_drop"
    hist_peak = peak_util
    if history:
        vals = [h.util_pct for h in history if h.util_pct is not None]
        if vals:
            hist_peak = max(hist_peak or 0.0, max(vals))
    if (
        util is not None
        and hist_peak is not None
        and hist_peak >= RTX_HIGH_UTIL
        and util <= hist_peak - SUDDEN_UTIL_DROP_POINTS
        and util < 50.0
    ):
        return True, "sudden_util_drop"
    if igpu.temp_c is not None and igpu.temp_c >= HOT_IGPU_ALONE_C:
        return True, "igpu_too_hot"
    return False, "healthy"

def estimate_amd_extra_bytes(sample: SystemSample, mode: str) -> int:
    gpu = solo_gpu(sample)
    gtt = int((gpu.vram_used_mb or 256.0) * 1024 * 1024)
    growth = min(max(gtt // 5, 64 * 1024 * 1024), 384 * 1024 * 1024)
    if mode == "PAUSE":
        return 0
    if mode == "RECOVER":
        return growth // 3
    return growth

def collect_amd_safety_reasons(sample: SystemSample, extra: int) -> List[str]:
    reasons: List[str] = []
    mem = sample.memory
    gpu = solo_gpu(sample)
    if mem.avail_bytes < MIN_RAM_AVAIL_BYTES:
        reasons.append("ram_below_1_5gb")
    if mem.total_bytes > 0 and mem.avail_ratio < MIN_RAM_AVAIL_RATIO:
        reasons.append("ram_below_8pct")
    leftover = mem.avail_bytes - extra
    leftover_ratio = leftover / float(mem.total_bytes) if mem.total_bytes else 1.0
    if leftover < MIN_LEFTOVER_AFTER_HYBRID or leftover_ratio < MIN_RAM_AVAIL_RATIO:
        reasons.append("igpu_shared_ram_would_starve")
    if (
        mem.commit_used_bytes is not None
        and mem.commit_limit_bytes is not None
        and mem.commit_limit_bytes > 0
    ):
        commit_ratio = mem.commit_used_bytes / float(mem.commit_limit_bytes)
        if commit_ratio >= COMMIT_PRESSURE_RATIO:
            reasons.append("commit_pressure")
        commit_avail = mem.commit_limit_bytes - mem.commit_used_bytes
        if commit_avail < extra + 512 * 1024 * 1024:
            reasons.append("commit_headroom_low")
    if gpu.temp_c is not None and gpu.temp_c >= HOT_IGPU_ALONE_C and mem.avail_ratio < 0.15:
        reasons.append("igpu_hot_and_ram_tight")
    return reasons

def evaluate_amd_policy(
    sample: SystemSample,
    history: Sequence[GpuSample] | None = None,
    pause: Optional[PauseState] = None,
    now: Optional[float] = None,
    peak_util: Optional[float] = None,
) -> PolicyDecision:
    now = time.time() if now is None else now
    pause = pause or PauseState()
    gpu = solo_gpu(sample)
    weak, weak_reason = igpu_is_weak(
        gpu,
        history=history,
        peak_util=peak_util,
        power_source=sample.power_source,
    )
    tentative = "RECOVER" if weak else "BOOST"
    extra = estimate_amd_extra_bytes(sample, tentative)
    reasons = collect_amd_safety_reasons(sample, extra)

    def _pause(reason: str, extra_reasons: List[str], left: float) -> PolicyDecision:
        return PolicyDecision(
            mode="PAUSE",
            reason=reason,
            igpu_assist=False,
            assist_level="off",
            budget_bytes=0,
            pause_seconds_left=left,
            safety_reasons=extra_reasons,
            extra_bytes_est=extra,
            amd_perf="low" if sample.memory.avail_bytes < MIN_RAM_AVAIL_BYTES else "auto",
            profile="amd_igpu",
        )

    if pause.active(now):
        return _pause(pause.reason or "safety_pause", list(reasons) if reasons else [pause.reason], pause.remaining(now))
    if reasons:
        return _pause(reasons[0], reasons, PAUSE_SECONDS)
    if weak:
        return PolicyDecision(
            mode="RECOVER",
            reason=weak_reason,
            igpu_assist=False,
            assist_level="off",
            budget_bytes=0,
            pause_seconds_left=0.0,
            safety_reasons=[],
            extra_bytes_est=extra,
            amd_perf="auto",
            profile="amd_igpu",
        )
    return PolicyDecision(
        mode="BOOST",
        reason="igpu_healthy_max_optimize",
        igpu_assist=False,
        assist_level="off",
        budget_bytes=0,
        pause_seconds_left=0.0,
        safety_reasons=[],
        extra_bytes_est=extra,
        amd_perf="high",
        profile="amd_igpu",
    )

def evaluate_policy(
    sample: SystemSample,
    history: Sequence[GpuSample] | None = None,
    pause: Optional[PauseState] = None,
    now: Optional[float] = None,
    peak_power_limit_w: Optional[float] = None,
    peak_util: Optional[float] = None,
    force_amd: bool = False,
    prev_copies: Optional[int] = None,
) -> PolicyDecision:
    if resolve_profile(sample, force_amd=force_amd) == "amd_igpu":
        return evaluate_amd_policy(
            sample,
            history=history,
            pause=pause,
            now=now,
            peak_util=peak_util,
        )
    now = time.time() if now is None else now
    pause = pause or PauseState()
    weak, weak_reason = rtx_is_weak(
        sample.rtx,
        history=history,
        peak_power_limit_w=peak_power_limit_w,
        peak_util=peak_util,
    )
    work = infer_workload(sample)
    dual = work.kind in {"game", "render", "mixed"}
    misplaced = dual and game_misplaced_on_igpu(sample)
    on_primary = False if misplaced else (game_on_nvidia(sample) if dual else True)
    ready = False if misplaced else (rtx_ready_for_igpu(sample) if dual else False)
    if dual and (
        not on_primary
        or float(sample.rtx.util_pct if sample.rtx.util_pct is not None else 0.0) < TARGET_RTX_MIN
    ):
        weak = False
        weak_reason = "healthy"
    tentative_mode = "BACKUP" if (weak and dual) else "BOOST"
    budget = compute_budget_bytes(sample, tentative_mode) if dual else 0
    reasons, extra = collect_safety_reasons(sample, budget)
    target_exe = _target_exe(work.target)
    target_name = work.target.name if work.target else ""

    def _paused(reason: str, extra_reasons: List[str], left: float) -> PolicyDecision:
        return PolicyDecision(
            mode="PAUSE",
            reason=reason,
            igpu_assist=False,
            assist_level="off",
            budget_bytes=0,
            pause_seconds_left=left,
            safety_reasons=extra_reasons,
            rtx_weak=weak,
            rtx_weak_reason=weak_reason,
            extra_bytes_est=extra,
            workload_kind=work.kind,
            target_exe=target_exe,
            target_name=target_name,
        )

    if pause.active(now):
        left = pause.remaining(now)
        return _paused(
            pause.reason or "safety_pause",
            list(reasons) if reasons else [pause.reason],
            left,
        )

    if reasons:
        return _paused(reasons[0], reasons, PAUSE_SECONDS)

    if dual:
        geo_mode = "BACKUP" if weak else "BOOST"
        w, h, ring = assist_geometry(geo_mode, budget)
        desired = assist_copies(sample, work, mode=geo_mode)
        sleep_ms = assist_sleep_ms(sample)
        can_assist = budget > 0 and ring >= 2
        reason = f"dual_gpu_{work.kind}"
        level = "remainder"
        step = "hold"
        copies = desired
        if not on_primary:
            can_assist = False
            desired = 0
            copies = 0
            sleep_ms = 0
            w, h, ring = 0, 0, 0
            reason = "misplaced_on_igpu" if misplaced else "game_off_nvidia"
            level = "misplaced" if misplaced else "yield"
            step = "off"
        elif not ready:
            can_assist = False
            desired = 0
            copies = 0
            sleep_ms = 0
            w, h, ring = 0, 0, 0
            reason = "nvidia_first"
            level = "nvidia_first"
            step = "off"
            if is_roblox_workload(sample, work):
                reason = "roblox_fps_500"
        else:
            ease = igpu_ease_needed(sample)
            climb_ok = ram_climb_ok(sample, extra)
            if ease:
                step = "ease"
                level = "ease"
                reason = "igpu_ease"
            elif not climb_ok:
                step = "hold"
                level = "hold"
                reason = "ram_hold_igpu"
                if prev_copies is not None:
                    desired = min(desired, max(ASSIST_MIN_COPIES, prev_copies))
            elif prev_copies is not None and desired > prev_copies:
                step = "climb"
                level = "climb"
                reason = "igpu_climb"
            else:
                step = "hold"
                level = "remainder"
                reason = "rtx_primary_remainder"
            if not weak:
                if is_roblox_workload(sample, work):
                    reason = "roblox_fps_500"
                    if step == "ease":
                        level = "ease"
                    elif step == "climb":
                        level = "climb"
                    else:
                        level = "fpsmax"
            if prev_copies is not None:
                copies = step_assist_copies(prev_copies, desired)
            else:
                copies = desired
            if can_assist and copies < ASSIST_MIN_COPIES and desired >= ASSIST_MIN_COPIES:
                copies = ASSIST_MIN_COPIES
            can_assist = can_assist and copies >= 1
        if weak:
            if not can_assist:
                return _paused("assist_budget_too_small", ["assist_budget_too_small"], PAUSE_SECONDS)
            return PolicyDecision(
                mode="BACKUP",
                reason=weak_reason,
                igpu_assist=True,
                assist_level="high",
                budget_bytes=budget,
                pause_seconds_left=0.0,
                safety_reasons=[],
                rtx_weak=True,
                rtx_weak_reason=weak_reason,
                extra_bytes_est=extra,
                width=w,
                height=h,
                ring=ring,
                rtx_boost=True,
                gpu_pref=2,
                workload_kind=work.kind,
                target_exe=target_exe,
                target_name=target_name,
                copies=copies,
                sleep_ms=sleep_ms,
                on_primary=on_primary,
                assist_step=step if step != "off" else "climb",
            )
        return PolicyDecision(
            mode="BOOST",
            reason=reason,
            igpu_assist=can_assist,
            assist_level=level,
            budget_bytes=budget if can_assist else 0,
            pause_seconds_left=0.0,
            safety_reasons=[],
            rtx_weak=False,
            rtx_weak_reason="healthy",
            extra_bytes_est=extra if can_assist else 0,
            width=w if can_assist else 0,
            height=h if can_assist else 0,
            ring=ring if can_assist else 0,
            rtx_boost=True,
            gpu_pref=2,
            workload_kind=work.kind,
            target_exe=target_exe,
            target_name=target_name,
            copies=copies if can_assist else 0,
            sleep_ms=sleep_ms if can_assist else 0,
            on_primary=on_primary,
            relaunch_game=bool(
                can_relaunch_misplaced(sample, work)
                and not on_primary
                and (
                    misplaced
                    or float(sample.rtx.vram_used_mb or 0.0) < RTX_GAME_VRAM_MB
                )
            ),
            assist_step=step,
        )

    if work.kind == "idle":
        return PolicyDecision(
            mode="BOOST",
            reason="idle_watch",
            igpu_assist=False,
            assist_level="watch",
            budget_bytes=0,
            pause_seconds_left=0.0,
            safety_reasons=[],
            rtx_weak=False,
            rtx_weak_reason=weak_reason,
            extra_bytes_est=0,
            rtx_boost=False,
            gpu_pref=None,
            workload_kind="idle",
            target_exe=target_exe,
            target_name=target_name,
        )
    if work.kind == "video":
        return PolicyDecision(
            mode="BOOST",
            reason="video_igpu_decode",
            igpu_assist=False,
            assist_level="eco",
            budget_bytes=0,
            pause_seconds_left=0.0,
            safety_reasons=[],
            rtx_weak=False,
            rtx_weak_reason=weak_reason,
            extra_bytes_est=0,
            rtx_boost=False,
            gpu_pref=1,
            workload_kind="video",
            target_exe=target_exe,
            target_name=target_name,
        )
    return PolicyDecision(
        mode="BOOST",
        reason="rtx_healthy_watch",
        igpu_assist=False,
        assist_level="watch",
        budget_bytes=0,
        pause_seconds_left=0.0,
        safety_reasons=[],
        rtx_weak=False,
        rtx_weak_reason="healthy",
        extra_bytes_est=0,
        rtx_boost=False,
        gpu_pref=None,
        workload_kind=work.kind,
        target_exe=target_exe,
        target_name=target_name,
    )

def apply_pause_transition(
    decision: PolicyDecision,
    pause: PauseState,
    now: float,
) -> PauseState:
    if decision.mode != "PAUSE":
        return PauseState(until=0.0, reason="")
    if pause.active(now):
        return pause
    return PauseState(until=now + PAUSE_SECONDS, reason=decision.reason)

GPU_PREF_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
RESTORE_BUNDLE = Path(tempfile.gettempdir()) / f"hybrid_gpu_restore_{os.getpid()}.json"

def _is_access_denied(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 5:
        return True
    text = str(exc).lower()
    return "access is denied" in text or "액세스가 거부" in text or "winerror 5" in text

def windows_is_elevated() -> Optional[bool]:
    if os.name != "nt":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None

class ApplyAbort(RuntimeError):
    pass

class RegistryGuard:

    def __init__(self) -> None:
        self.aborted = False
        self.abort_reason = ""
        self.restored = False
        self._pref_original: Dict[str, Optional[str]] = {}
        self._pref_written: List[str] = []
        self.nvidia_original_pl: Optional[int] = None
        self.nvidia_clocks_locked: bool = False
        self.nvidia_unlock_done: bool = False
        self.nvidia_unlock_last_try: float = 0.0
        self.amd_original_perf: Dict[str, str] = {}
        self.file_originals: Dict[str, Optional[str]] = {}
        self.store: Optional[Dict[str, Optional[str]]] = None
        self.power_plan_done: bool = False
        self.game_mode_done: bool = False
        self.roblox_xml_locked: bool = False

    def _persist(self) -> None:
        payload = {
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "pref_original": self._pref_original,
            "pref_written": self._pref_written,
            "nvidia_original_pl": self.nvidia_original_pl,
            "nvidia_clocks_locked": self.nvidia_clocks_locked,
            "amd_original_perf": self.amd_original_perf,
            "file_originals": self.file_originals,
        }
        try:
            RESTORE_BUNDLE.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def load_crash_bundle(self) -> bool:
        if not RESTORE_BUNDLE.is_file():
            return False
        try:
            payload = json.loads(RESTORE_BUNDLE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        self._pref_original = dict(payload.get("pref_original") or {})
        self._pref_written = list(payload.get("pref_written") or [])
        self.nvidia_original_pl = payload.get("nvidia_original_pl")
        self.nvidia_clocks_locked = bool(payload.get("nvidia_clocks_locked"))
        self.amd_original_perf = dict(payload.get("amd_original_perf") or {})
        self.file_originals = dict(payload.get("file_originals") or {})
        return True

    def abort(self, reason: str) -> None:
        if not self.aborted:
            self.aborted = True
            self.abort_reason = reason
            log(f"ABORT — {reason}. Restored snapshot.")
        self.restore()

    def restore(self) -> None:
        errors: List[str] = []
        for name, original in reversed(list(self._pref_original.items())):
            if name.startswith("HKLM:"):
                continue
            try:
                self._restore_pref(name, original)
            except Exception as exc:
                if _is_access_denied(exc):
                    continue
                errors.append(f"pref {name}: {exc}")
        if self.nvidia_original_pl is not None:
            try:
                _nvidia_set_power_limit(self.nvidia_original_pl)
            except Exception as exc:
                errors.append(f"nvidia-pl: {exc}")
        if self.nvidia_clocks_locked:
            try:
                _nvidia_reset_clocks()
            except Exception as exc:
                errors.append(f"nvidia-clk: {exc}")
            self.nvidia_clocks_locked = False
        self.nvidia_unlock_done = False
        self.nvidia_unlock_last_try = 0.0
        for path, value in self.amd_original_perf.items():
            try:
                Path(path).write_text(value + "\n", encoding="utf-8")
            except OSError as exc:
                errors.append(f"amd {path}: {exc}")
        for path, original in self.file_originals.items():
            try:
                dest = Path(path)
                if original is None:
                    if dest.is_file():
                        dest.unlink()
                else:
                    dest.write_text(original, encoding="utf-8")
            except OSError as exc:
                errors.append(f"file {path}: {exc}")
        self.file_originals.clear()
        self.restored = True
        self._pref_written.clear()
        try:
            if RESTORE_BUNDLE.is_file():
                RESTORE_BUNDLE.unlink()
        except OSError:
            pass
        if errors:
            log("restore errors: " + "; ".join(errors))
        else:
            log("Restored settings to the pre-apply snapshot.")

    def _read_pref(self, name: str) -> Optional[str]:
        if self.store is not None:
            return self.store.get(name)
        if os.name != "nt":
            return None
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, GPU_PREF_KEY, 0, winreg.KEY_READ)
            try:
                val, _typ = winreg.QueryValueEx(key, name)
                return str(val)
            finally:
                winreg.CloseKey(key)
        except OSError:
            return None

    def _write_pref_raw(self, name: str, value: Optional[str], *, restoring: bool = False) -> None:
        if self.store is not None:
            if value is not None and not restoring and ("GpuPreference=" not in value or value.count("=") > 2):
                raise ApplyAbort("registry_value_corrupt")
            if value is None:
                self.store.pop(name, None)
            else:
                self.store[name] = value
            return
        if os.name != "nt":
            return
        import winreg

        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, GPU_PREF_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if value is None:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                if not restoring and "GpuPreference=" not in value:
                    raise ApplyAbort("registry_value_corrupt")
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        finally:
            winreg.CloseKey(key)

    def _restore_pref(self, name: str, original: Optional[str]) -> None:
        self._write_pref_raw(name, original, restoring=True)

    def set_gpu_preference(self, exe_path: str, preference: int, *, fatal: bool = True) -> bool:
        if self.aborted:
            if fatal:
                raise ApplyAbort(self.abort_reason or "already_aborted")
            return False
        if preference not in (0, 1, 2):
            if fatal:
                self.abort("invalid_gpu_preference")
                raise ApplyAbort(self.abort_reason)
            log("warning: GpuPreference value out of range")
            return False
        if not _pinable_gpu_pref_name(exe_path):
            return False
        name = exe_path
        value = f"GpuPreference={preference};"
        try:
            original = self._read_pref(name)
            self._write_pref_raw(name, value)
            if name not in self._pref_original:
                self._pref_original[name] = original
            self._pref_written.append(name)
            self._persist()
            return True
        except ApplyAbort as exc:
            if fatal:
                self.abort(str(exc))
                raise
            log(f"warning: GPU preference failed ({exc}) — continuing")
            return False
        except OSError as exc:
            if fatal:
                self.abort(f"registry_os_error:{exc}")
                raise ApplyAbort(self.abort_reason) from exc
            log(f"warning: GPU preference OS error ({exc}) — continuing")
            return False

    def remember_nvidia_pl(self, current: Optional[float]) -> None:
        if self.nvidia_original_pl is None and current is not None:
            self.nvidia_original_pl = int(round(current))
            self._persist()

    def mark_nvidia_clocks_locked(self) -> None:
        if not self.nvidia_clocks_locked:
            self.nvidia_clocks_locked = True
            self._persist()

    def remember_amd_perf(self, path: str, current: str) -> None:
        if path not in self.amd_original_perf:
            self.amd_original_perf[path] = current
            self._persist()

    def remember_file(self, path: str, original: Optional[str]) -> None:
        if path not in self.file_originals:
            self.file_originals[path] = original
            self._persist()

def _nvidia_set_power_limit(watts: int) -> None:
    smi = _which_nvidia_smi()
    if not smi:
        raise ApplyAbort("nvidia_smi_missing")
    proc = subprocess.run(
        [smi, "-pl", str(int(watts))],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if proc.returncode != 0:
        raise ApplyAbort((proc.stderr or proc.stdout or "nvidia-smi -pl failed").strip())

def _nvidia_smi_args(*args: str) -> Tuple[bool, str]:
    smi = _which_nvidia_smi()
    if not smi:
        return False, "nvidia_smi_missing"
    try:
        proc = subprocess.run(
            [smi, *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, text

def try_nvidia_set_power_limit(watts: int) -> Tuple[bool, str]:
    try:
        _nvidia_set_power_limit(watts)
        return True, f"nvidia-smi -pl {int(watts)}W"
    except ApplyAbort as exc:
        return False, f"warning: power limit {int(watts)}W failed ({exc})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"warning: power limit OS error ({exc})"

def _nvidia_reset_clocks() -> None:
    _nvidia_smi_args("-rgc")
    _nvidia_smi_args("-rmc")

def read_nvidia_power_max_w() -> Optional[float]:
    smi = _which_nvidia_smi()
    if not smi:
        return None
    try:
        proc = subprocess.run(
            [smi, "--query-gpu=power.max_limit", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _parse_smi_num(proc.stdout.strip().splitlines()[0])

def read_nvidia_mem_max_mhz() -> Optional[float]:
    smi = _which_nvidia_smi()
    if not smi:
        return None
    try:
        proc = subprocess.run(
            [smi, "--query-gpu=clocks.max.mem", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _parse_smi_num(proc.stdout.strip().splitlines()[0])

def nvidia_power_unlock_watts(current: Optional[float], max_w: Optional[float]) -> List[int]:
    out: List[int] = []
    for raw in (max_w, (current or 0) + 80, (current or 0) + 60, (current or 0) + 40, (current or 0) + 20, 200, 175, 160, 140, 125, 115, TGP_HINT_W, current):
        if raw is None or raw <= 0:
            continue
        watts = int(round(raw))
        if 30 <= watts <= 250 and watts not in out:
            out.append(watts)
    out.sort(reverse=True)
    return out

def roblox_client_settings_files() -> List[Path]:
    files: List[Path] = []
    local = os.environ.get("LOCALAPPDATA") or ""
    if not local:
        return files
    root = Path(local) / "Roblox"
    files.append(root / "ClientSettings" / "ClientAppSettings.json")
    versions = root / "Versions"
    if versions.is_dir():
        for folder in versions.iterdir():
            if folder.is_dir():
                files.append(folder / "ClientSettings" / "ClientAppSettings.json")
    return files

def roblox_global_settings_xml() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA") or ""
    if not local:
        return None
    path = Path(local) / "Roblox" / "GlobalBasicSettings_13.xml"
    return path if path.is_file() else None

def merge_roblox_client_flags(path: Path, guard: RegistryGuard) -> bool:
    key = str(path)
    original: Optional[str] = None
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            original = path.read_text(encoding="utf-8")
            loaded = json.loads(original)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError, UnicodeError):
            original = path.read_text(encoding="utf-8") if path.is_file() else None
            data = {}
    changed = False
    for flag, value in ROBLOX_CLIENT_FLAGS.items():
        if data.get(flag) != value:
            data[flag] = value
            changed = True
    if not changed and path.is_file():
        return False
    guard.remember_file(key, original)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True

def bump_roblox_xml_fps(text: str, fps: int = ROBLOX_TARGET_FPS) -> str:
    updated, n = re.subn(
        r'(<int name="FramerateCap">)\s*\d+\s*(</int>)',
        rf"\g<1>{fps}\2",
        text,
        flags=re.IGNORECASE,
    )
    if n:
        return updated
    updated, n = re.subn(
        r'(<item name="FramerateCap"[^>]*>\s*<int>)\s*\d+\s*(</int>)',
        rf"\g<1>{fps}\2",
        text,
        flags=re.IGNORECASE,
    )
    return updated if n else text

def apply_roblox_fps_unlock(guard: RegistryGuard, *, dry_run: bool = False) -> List[str]:
    notes: List[str] = []
    paths = roblox_client_settings_files()
    if dry_run:
        notes.append(f"[dry-run] Roblox FPS cap {ROBLOX_TARGET_FPS} → ClientAppSettings.json x {len(paths)}")
        return notes
    wrote = 0
    for path in paths:
        try:
            if merge_roblox_client_flags(path, guard):
                wrote += 1
        except OSError as exc:
            notes.append(f"warning: Roblox settings write failed ({path.name}: {exc})")
    if wrote:
        notes.append(f"Roblox FPS cap {ROBLOX_TARGET_FPS} — wrote {wrote} ClientAppSettings")
    xml_path = roblox_global_settings_xml()
    if xml_path is not None:
        try:
            original = xml_path.read_text(encoding="utf-8")
            updated = bump_roblox_xml_fps(original)
            if updated != original:
                guard.remember_file(str(xml_path), original)
                xml_path.write_text(updated, encoding="utf-8")
                notes.append(f"Roblox GlobalBasicSettings FramerateCap={ROBLOX_TARGET_FPS}")
        except OSError:
            if not guard.roblox_xml_locked:
                notes.append(
                    "Roblox XML is locked; ClientAppSettings FPS 500 only"
                )
            guard.roblox_xml_locked = True
    return notes

def apply_heavy_process_priority(sample: SystemSample, *, dry_run: bool = False) -> List[str]:
    names: List[str] = []
    seen = set()
    for proc in sample.hot_procs or []:
        if proc.kind not in {"game", "render"} and not is_gpu_heavy_name(proc.name, proc.exe_path):
            continue
        stem = _exe_basename(proc.name or proc.exe_path)
        if stem.lower().endswith(".exe"):
            stem = stem[:-4]
        key = stem.lower()
        if not stem or key in seen or "crash" in key:
            continue
        seen.add(key)
        names.append(stem)
    if not names:
        names = ["RobloxPlayerBeta", "VALORANT-Win64-Shipping", "webots"]
    if dry_run:
        return [f"[dry-run] GPU-heavy process priority High ({', '.join(names[:4])})"]
    if os.name != "nt":
        return []
    quoted = ",".join("'" + n.replace("'", "''") + "'" for n in names)
    script = (
        f"Get-Process -Name {quoted} -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.PriorityClass = 'High' }"
    )
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode == 0:
        return [f"GPU-heavy process priority High ({len(names)})"]
    return []

def apply_roblox_process_priority(*, dry_run: bool = False) -> List[str]:
    if dry_run:
        return ["[dry-run] GPU-heavy process priority High (RobloxPlayerBeta, VALORANT-Win64-Shipping, webots)"]
    if os.name != "nt":
        return []
    script = (
        "Get-Process -Name 'RobloxPlayerBeta','RobloxPlayer','RobloxPlayerLauncher',"
        "'VALORANT-Win64-Shipping','VALORANT','webots','webots-bin','coppeliaSim',"
        "'Unity','UnrealEditor' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.PriorityClass = 'High' }"
    )
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode == 0:
        return ["GPU-heavy process priority High"]
    return []

def apply_high_performance_power(guard: RegistryGuard, *, dry_run: bool = False) -> List[str]:
    if dry_run:
        return ["[dry-run] powercfg High performance"]
    if guard.power_plan_done or os.name != "nt":
        return []
    guard.power_plan_done = True
    try:
        proc = subprocess.run(
            ["powercfg", "/setactive", HIGH_PERF_POWER_GUID],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode == 0:
        return ["power plan High performance"]
    return []

def apply_windows_game_mode(guard: RegistryGuard, *, dry_run: bool = False) -> List[str]:
    if dry_run:
        return ["[dry-run] Windows Game Mode on"]
    if guard.game_mode_done or os.name != "nt":
        return []
    try:
        import winreg

        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\GameBar",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.SetValueEx(key, "AllowAutoGameMode", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "AutoGameModeEnabled", 0, winreg.REG_DWORD, 1)
        finally:
            winreg.CloseKey(key)
        guard.game_mode_done = True
        return ["Windows Game Mode on"]
    except OSError:
        return []

def apply_workload_boost(
    decision: PolicyDecision,
    sample: SystemSample,
    guard: RegistryGuard,
    *,
    dry_run: bool = False,
) -> List[str]:
    notes: List[str] = []
    if decision.mode == "PAUSE":
        return notes
    if decision.gpu_pref in (1, 2):
        label = "high performance (dGPU)" if decision.gpu_pref == 2 else "power saving (iGPU)"
        targets = preference_pin_names(sample, decision)
        pinned: List[str] = []
        if dry_run:
            notes.append(f"[dry-run] GpuPreference={decision.gpu_pref} ({label}) × {len(targets)}")
            for exe in targets[:4]:
                notes.append(f"[dry-run]   {exe}")
            pinned = list(targets)
        else:
            for exe in targets:
                if guard.set_gpu_preference(exe, int(decision.gpu_pref), fatal=False):
                    pinned.append(exe)
            if pinned:
                notes.append(f"GpuPreference={decision.gpu_pref} ({label}) pinned {len(pinned)}")
        if decision.gpu_pref == 2:
            leftovers = remainder_pin_names(sample)
            leftover: List[str] = []
            if dry_run:
                leftover = [exe for exe in leftovers if exe not in pinned]
                notes.append(f"[dry-run] GpuPreference=1 (iGPU leftover) x {len(leftover)}")
            else:
                for exe in leftovers:
                    if exe in pinned:
                        continue
                    if guard.set_gpu_preference(exe, 1, fatal=False):
                        leftover.append(exe)
                if leftover:
                    notes.append(f"GpuPreference=1 (iGPU leftover) pinned {len(leftover)}")
        if decision.reason == "misplaced_on_igpu":
            notes.append(
                "misplaced — iGPU is drawing a GPU-heavy app. "
                "Wrote GpuPreference; relaunching Webots/Valorant/Roblox onto RTX"
            )
            if dry_run:
                notes.extend(relaunch_heavy_onto_dgpu(dry_run=True, prefer=decision.target_name))
        elif not decision.on_primary and decision.gpu_pref == 2:
            notes.append("game is not on NVIDIA SM — quit and relaunch the process")
        elif decision.assist_level == "nvidia_first":
            notes.append("NVIDIA first — iGPU assist off until dGPU is 50%+")
        elif decision.assist_level in {"fpsmax", "remainder", "climb", "hold", "ease"}:
            extra = f" Roblox target {ROBLOX_TARGET_FPS} FPS" if is_roblox_workload(sample) else " GPU-heavy app"
            if decision.assist_level == "ease":
                notes.append(f"strain — easing iGPU assist.{extra}")
            elif decision.assist_level == "climb":
                notes.append(f"dGPU maxed; climbing iGPU assist.{extra}")
            else:
                notes.append(f"max frames — dGPU first, iGPU remainder.{extra}")
    if is_gpu_heavy_workload(sample) or (decision.rtx_boost and decision.gpu_pref == 2):
        if is_roblox_workload(sample):
            notes.extend(apply_roblox_fps_unlock(guard, dry_run=dry_run))
        notes.extend(apply_heavy_process_priority(sample, dry_run=dry_run))
        notes.extend(apply_high_performance_power(guard, dry_run=dry_run))
        notes.extend(apply_windows_game_mode(guard, dry_run=dry_run))
    if not decision.rtx_boost:
        return notes
    if sample.rtx.vendor != "nvidia":
        return notes
    max_w = read_nvidia_power_max_w()
    current = sample.rtx.power_limit_w
    candidates = nvidia_power_unlock_watts(current, max_w)
    lo_clk, hi_clk = nvidia_clock_range(sample.rtx.max_clock_mhz)
    if dry_run:
        if candidates:
            notes.append(f"[dry-run] nvidia-smi -pl {candidates[0]}W (unlock tries {candidates[:4]})")
        if lo_clk and hi_clk:
            notes.append(f"[dry-run] nvidia-smi -lgc {lo_clk},{hi_clk}")
        notes.append("[dry-run] nvidia-smi -pm 1")
        return notes
    if not should_retry_nvidia_unlock(guard, sample):
        return notes
    retrying = guard.nvidia_unlock_done
    if retrying:
        notes.append(
            f"NVIDIA unlock retry — RTX {fmt_opt(sample.rtx.util_pct, '%')} "
            f"< {int(RTX_STARVED_UTIL)}%"
        )
    ok_pm, _pm = _nvidia_smi_args("-pm", "1")
    if ok_pm:
        notes.append("nvidia-smi -pm 1")
    guard.remember_nvidia_pl(current)
    applied_pl = None
    last_pl_msg = ""
    for watts in candidates:
        ok, msg = try_nvidia_set_power_limit(watts)
        last_pl_msg = msg
        if ok:
            applied_pl = watts
            notes.append(f"NVIDIA power limit {fmt_opt(current, 'W', 0)} -> {watts}W")
            break
    if applied_pl is None and last_pl_msg:
        notes.append(last_pl_msg + " — continuing")
    if lo_clk and hi_clk:
        ok_c, msg_c = _nvidia_smi_args("-lgc", f"{lo_clk},{hi_clk}")
        if ok_c:
            guard.mark_nvidia_clocks_locked()
            notes.append(f"NVIDIA graphics clock locked {lo_clk}-{hi_clk}MHz")
        else:
            notes.append(f"warning: clock lock failed ({msg_c}) — continuing")
        mem_max = read_nvidia_mem_max_mhz()
        if mem_max and mem_max >= 1000:
            lo_m, hi_m = nvidia_clock_range(mem_max, NVIDIA_MEM_CLOCK_FLOOR_RATIO)
            ok_m, msg_m = _nvidia_smi_args("-lmc", f"{lo_m},{hi_m}")
            if ok_m:
                guard.mark_nvidia_clocks_locked()
                notes.append(f"NVIDIA memory clock locked {lo_m}-{hi_m}MHz")
            elif msg_m:
                notes.append(f"warning: memory clock lock failed ({msg_m}) — continuing")
    guard.nvidia_unlock_done = True
    guard.nvidia_unlock_last_try = time.time()
    return notes

def apply_amd_perf_level(level: str, guard: RegistryGuard, dry_run: bool) -> List[str]:
    notes: List[str] = []
    if guard.aborted:
        return ["apply locked — not changing perf level after abort"]
    if level not in {"high", "auto", "low"}:
        guard.abort(f"invalid_amd_perf:{level}")
        return [guard.abort_reason]
    if dry_run:
        return [f"[dry-run] solo GPU perf level -> {level}"]
    drm = Path("/sys/class/drm")
    if drm.is_dir():
        for card in drm.glob("card[0-9]*/device/power_dpm_force_performance_level"):
            vendor = card.parent / "vendor"
            try:
                if vendor.read_text(encoding="utf-8").strip().lower() not in {"0x1002", "0x1022"}:
                    continue
            except OSError:
                continue
            try:
                current = card.read_text(encoding="utf-8").strip()
                guard.remember_amd_perf(str(card), current)
                card.write_text(level + "\n", encoding="utf-8")
                notes.append(f"{card} = {level}")
            except OSError as exc:
                guard.abort(f"amd_sysfs_error:{exc}")
                notes.append(guard.abort_reason)
                return notes
    if not notes:
        notes.append("no perf-level path or permission (monitor only).")
    return notes

def _which_nvidia_smi() -> Optional[str]:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    extras = [
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        "/usr/bin/nvidia-smi",
    ]
    for p in extras:
        if os.path.isfile(p):
            return p
    return None

def _parse_smi_num(raw: str, *, as_mb: bool = False) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw or raw.upper() in ("N/A", "[N/A]", "NA"):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    if as_mb:
        low = raw.lower()
        if "gi" in low or re.search(r"\bgb\b", low):
            val *= 1024.0
        elif "ki" in low or re.search(r"\bkb\b", low):
            val /= 1024.0
    return val

def parse_nvidia_smi_row(line: str) -> Optional[GpuSample]:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 9:
        return None
    nums = parts[-8:]
    name = ", ".join(parts[:-8]).strip() or "NVIDIA GPU"
    vram = _parse_smi_num(nums[7], as_mb=True)
    return GpuSample(
        name=name,
        vendor="nvidia",
        util_pct=_parse_smi_num(nums[0]),
        clock_mhz=_parse_smi_num(nums[1]),
        max_clock_mhz=_parse_smi_num(nums[2]),
        power_w=_parse_smi_num(nums[3]),
        power_limit_w=_parse_smi_num(nums[4]),
        temp_c=_parse_smi_num(nums[5]),
        vram_used_mb=_parse_smi_num(nums[6], as_mb=True),
        vram_total_mb=vram,
        present=True,
        vendor_id=NVIDIA_VENDOR_ID,
        discrete=not looks_integrated(name, vram),
    )

def read_nvidia_gpus() -> List[GpuSample]:
    smi = _which_nvidia_smi()
    if not smi:
        return []
    query = (
        "name,utilization.gpu,clocks.gr,clocks.max.gr,"
        "power.draw,power.limit,temperature.gpu,memory.used,memory.total"
    )
    try:
        proc = subprocess.run(
            [smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    out: List[GpuSample] = []
    for line in proc.stdout.strip().splitlines():
        gpu = parse_nvidia_smi_row(line)
        if gpu:
            out.append(gpu)
    return out

def read_nvidia() -> Optional[GpuSample]:
    gpus = read_nvidia_gpus()
    return gpus[0] if gpus else None

def _ps_json(script: str, timeout: float = 8.0) -> Any:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [
                exe,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None

def _controller_to_gpu(picked: Dict[str, Any]) -> Optional[GpuSample]:
    name = str(picked.get("Name") or "")
    if not name or looks_skip_adapter(name):
        return None
    pnp = str(picked.get("PNPDeviceID") or "")
    vendor = classify_vendor(name, pnp=pnp)
    adapter_ram = picked.get("AdapterRAM")
    vram_mb = None
    try:
        if adapter_ram is not None:
            vram_mb = int(adapter_ram) / (1024 * 1024)
            if vram_mb <= 0:
                vram_mb = None
    except (TypeError, ValueError):
        vram_mb = None
    vid = vendor_id_of(vendor)
    m = re.search(r"ven_([0-9a-f]{4})", pnp.lower())
    if m:
        vid = int(m.group(1), 16)
    return GpuSample(
        name=name,
        vendor=vendor,
        vram_total_mb=vram_mb,
        present=True,
        vendor_id=vid,
        discrete=not looks_integrated(name, vram_mb),
    )

def _pick_igpu_from_controllers(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for it in items:
        name = str(it.get("Name") or it.get("name") or "")
        if looks_skip_adapter(name):
            continue
        low = name.lower()
        if any(h in low for h in ("nvidia", "geforce", "rtx ", "gtx ")):
            continue
        if looks_integrated(name) or any(
            h in low for h in (
                "amd",
                "radeon",
                "intel",
                "iris",
                "uhd",
                "arc",
                "apple",
                "adreno",
                "mali",
                "powervr",
                "imagination",
                "videocore",
                "xclipse",
                "maleoon",
                "vivante",
                "asahi",
            )
        ):
            candidates.append(it)
    if not candidates:
        return None
    for it in candidates:
        if looks_integrated(str(it.get("Name") or "")):
            return it
    return candidates[0]

def read_windows_gpus() -> List[GpuSample]:
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, DriverVersion, Status, PNPDeviceID | "
        "ConvertTo-Json -Compress"
    )
    data = _ps_json(script, timeout=8.0)
    if data is None:
        return []
    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
    else:
        return []
    out: List[GpuSample] = []
    for it in items:
        gpu = _controller_to_gpu(it)
        if gpu and not looks_skip_adapter(gpu.name):
            gpu.util_pct = _read_windows_gpu_util(gpu.name)
            out.append(gpu)
    return out

def read_amd_windows() -> Optional[GpuSample]:
    gpus = read_windows_gpus()
    igpus = [g for g in gpus if looks_integrated(g.name, g.vram_total_mb)]
    if igpus:
        return igpus[0]
    non_nv = [g for g in gpus if g.vendor != "nvidia"]
    return non_nv[0] if non_nv else (gpus[0] if gpus else None)

def _read_windows_gpu_util(igpu_name: str) -> Optional[float]:
    script = r"""
$ErrorActionPreference='SilentlyContinue'
try {
  $c = Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction Stop
  $rows = @()
  foreach ($s in $c.CounterSamples) {
    $rows += [pscustomobject]@{ Path=$s.Path; Cooked=$s.CookedValue }
  }
  $rows | ConvertTo-Json -Compress
} catch { '' }
"""
    data = _ps_json(script, timeout=6.0)
    if not data:
        return None
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return None
    vals: List[float] = []
    for row in rows:
        path = str(row.get("Path") or "").lower()
        if "engtype_3d" not in path and "engtype_compute" not in path:
            continue
        try:
            vals.append(float(row.get("Cooked") or 0.0))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return max(0.0, min(100.0, max(vals)))

def parse_dpm_clocks(text: str) -> Tuple[Optional[float], Optional[float]]:
    current: Optional[float] = None
    max_mhz: Optional[float] = None
    for line in text.splitlines():
        found = re.search(r"(\d+(?:\.\d+)?)\s*(ghz|mhz|khz|hz)", line, re.I)
        if not found:
            continue
        val = float(found.group(1))
        unit = found.group(2).lower()
        if unit.startswith("g"):
            mhz = val * 1000.0
        elif unit.startswith("k"):
            mhz = val / 1000.0
        elif unit == "hz":
            mhz = val / 1_000_000.0
        else:
            mhz = val
        max_mhz = mhz if max_mhz is None else max(max_mhz, mhz)
        if "*" in line:
            current = mhz
    if current is None and max_mhz is not None:
        current = max_mhz
    return current, max_mhz

def _sysfs_first_float(paths: Sequence[Path]) -> Optional[float]:
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore").strip().split()[0]
            return float(raw)
        except (OSError, ValueError, IndexError):
            continue
    return None

def _sysfs_power_watts(paths: Sequence[Path]) -> Optional[float]:
    raw = _sysfs_first_float(paths)
    if raw is None or raw < 0:
        return None
    if raw >= 1_000_000.0:
        return raw / 1_000_000.0
    if raw >= 1_000.0:
        return raw / 1_000.0
    return raw

SKIP_DRM_DRIVERS = (
    "virtio_gpu",
    "vmwgfx",
    "qxl",
    "bochs-drm",
    "vboxvideo",
    "vgem",
    "vkms",
    "simple-framebuffer",
    "cirrus",
    "ast",
    "simpledrm",
    "efifb",
    "xen",
    "dummy",
    "udl",
    "hyperv_fb",
    "evdi",
)

def _read_sysfs_card(card_dir: Path) -> Optional[GpuSample]:
    vendor_path = card_dir / "device" / "vendor"
    if not vendor_path.is_file():
        return None
    try:
        vendor_hex = vendor_path.read_text(encoding="utf-8", errors="ignore").strip().lower()
    except OSError:
        return None
    vid_map = {
        "0x10de": ("nvidia", NVIDIA_VENDOR_ID),
        "0x1002": ("amd", AMD_VENDOR_ID),
        "0x1022": ("amd", AMD_VENDOR_ID),
        "0x8086": ("intel", INTEL_VENDOR_ID),
        "0x106b": ("apple", APPLE_VENDOR_ID),
        "0x17cb": ("qualcomm", QUALCOMM_VENDOR_ID),
        "0x5143": ("qualcomm", QUALCOMM_LEGACY_ID),
        "0x13b5": ("arm", ARM_VENDOR_ID),
        "0x1010": ("img", IMG_VENDOR_ID),
        "0x14e4": ("broadcom", BROADCOM_VENDOR_ID),
        "0x1ed5": ("moorethreads", MOORETHREADS_VENDOR_ID),
        "0x144d": ("samsung", SAMSUNG_VENDOR_ID),
        "0x19e5": ("huawei", HUAWEI_VENDOR_ID),
    }
    driver = ""
    uevent = card_dir / "device" / "uevent"
    try:
        txt = uevent.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"DRIVER=(.+)", txt)
        if m:
            driver = m.group(1).strip()
    except OSError:
        txt = ""
    if driver.lower() in SKIP_DRM_DRIVERS:
        return None
    if vendor_hex in vid_map:
        vendor, vendor_id = vid_map[vendor_hex]
    else:
        guessed = classify_vendor(driver or card_dir.name, pnp=vendor_hex)
        if guessed == "other":
            return None
        vendor, vendor_id = guessed, vendor_id_of(guessed)
    name = f"{vendor.upper()} GPU"
    if driver:
        name = f"{vendor.upper()} ({driver})"
    for label_path in (
        card_dir / "device" / "label",
        card_dir / "device" / "product_name",
    ):
        try:
            if label_path.is_file():
                label = label_path.read_text(encoding="utf-8", errors="ignore").strip()
                if label:
                    name = label
                    break
        except OSError:
            continue
    if looks_skip_adapter(name):
        return None
    util = _sysfs_first_float(
        (
            card_dir / "device" / "gpu_busy_percent",
            card_dir / "gt" / "gt0" / "busy",
            card_dir / "device" / "gt_busy_percent",
        )
    )
    if util is not None and util > 100.0:
        util = min(100.0, util / 100.0) if util <= 10000.0 else 100.0
    temp = None
    hwmon = card_dir / "device" / "hwmon"
    if hwmon.is_dir():
        for tfile in (
            *hwmon.glob("hwmon*/temp1_input"),
            *hwmon.glob("hwmon*/temp2_input"),
        ):
            try:
                milli = float(tfile.read_text(encoding="utf-8", errors="ignore").strip())
                temp = milli / 1000.0
                break
            except (OSError, ValueError):
                continue
    power_w = power_limit_w = None
    if hwmon.is_dir():
        power_w = _sysfs_power_watts(
            (
                *hwmon.glob("hwmon*/power1_average"),
                *hwmon.glob("hwmon*/power1_input"),
            )
        )
        power_limit_w = _sysfs_power_watts(
            (
                *hwmon.glob("hwmon*/power1_cap"),
                *hwmon.glob("hwmon*/power1_max"),
            )
        )
    vram_used = vram_total = None
    used_f = card_dir / "device" / "mem_info_vram_used"
    tot_f = card_dir / "device" / "mem_info_vram_total"
    try:
        if used_f.is_file():
            vram_used = int(used_f.read_text().strip()) / (1024 * 1024)
        if tot_f.is_file():
            vram_total = int(tot_f.read_text().strip()) / (1024 * 1024)
    except (OSError, ValueError):
        pass
    if vram_total is None:
        vis_tot = card_dir / "device" / "mem_info_vis_vram_total"
        vis_used = card_dir / "device" / "mem_info_vis_vram_used"
        try:
            if vis_tot.is_file():
                vram_total = int(vis_tot.read_text().strip()) / (1024 * 1024)
            if vis_used.is_file() and vram_used is None:
                vram_used = int(vis_used.read_text().strip()) / (1024 * 1024)
        except (OSError, ValueError):
            pass
    if vram_total is None:
        gtt_tot = card_dir / "device" / "mem_info_gtt_total"
        gtt_used = card_dir / "device" / "mem_info_gtt_used"
        try:
            if gtt_tot.is_file():
                vram_total = int(gtt_tot.read_text().strip()) / (1024 * 1024)
            if gtt_used.is_file() and vram_used is None:
                vram_used = int(gtt_used.read_text().strip()) / (1024 * 1024)
        except (OSError, ValueError):
            pass
    clock = None
    max_clock = None
    sclk = card_dir / "device" / "pp_dpm_sclk"
    try:
        if sclk.is_file():
            clock, max_clock = parse_dpm_clocks(
                sclk.read_text(encoding="utf-8", errors="ignore")
            )
    except OSError:
        pass
    if clock is None:
        clock = _sysfs_first_float(
            (
                card_dir / "gt" / "gt0" / "rps_act_freq_mhz",
                card_dir / "gt" / "gt0" / "freq_act",
                card_dir / "device" / "gt_act_freq_mhz",
                card_dir / "device" / "gt_cur_freq_mhz",
                card_dir / "device" / "gt_RP1_freq_mhz",
            )
        )
    if max_clock is None:
        max_clock = _sysfs_first_float(
            (
                card_dir / "gt" / "gt0" / "rps_max_freq_mhz",
                card_dir / "gt" / "gt0" / "rps_boost_freq_mhz",
                card_dir / "gt" / "gt0" / "freq_rp0",
                card_dir / "device" / "gt_max_freq_mhz",
                card_dir / "device" / "gt_RP0_freq_mhz",
            )
        )
    return GpuSample(
        name=name,
        vendor=vendor,
        util_pct=util,
        clock_mhz=clock,
        max_clock_mhz=max_clock,
        power_w=power_w,
        power_limit_w=power_limit_w,
        temp_c=temp,
        vram_used_mb=vram_used,
        vram_total_mb=vram_total,
        present=True,
        vendor_id=vendor_id,
        discrete=not looks_integrated(name, vram_total),
    )

def read_linux_gpus() -> List[GpuSample]:
    drm = Path("/sys/class/drm")
    if not drm.is_dir():
        return []
    out: List[GpuSample] = []
    for card in sorted(drm.glob("card[0-9]")):
        sample = _read_sysfs_card(card)
        if sample:
            out.append(sample)
    return out

def read_amd_linux() -> Optional[GpuSample]:
    for g in read_linux_gpus():
        if g.vendor == "amd":
            return g
    return None

def read_igpu() -> Optional[GpuSample]:
    if os.name == "nt":
        return read_amd_windows()
    gpus = read_linux_gpus()
    igpus = [g for g in gpus if looks_integrated(g.name, g.vram_total_mb) or g.vendor in {
        "amd", "intel", "apple", "qualcomm", "arm", "img",
    }]
    if igpus:
        return igpus[0]
    return gpus[0] if gpus else None

def merge_gpus(*groups: Sequence[GpuSample]) -> List[GpuSample]:
    by_key: Dict[str, GpuSample] = {}
    for group in groups:
        for gpu in group:
            if not gpu.present or looks_skip_adapter(gpu.name):
                continue
            key = re.sub(r"\s+", " ", gpu.name.lower()).strip()
            prev = by_key.get(key)
            if prev is None:
                by_key[key] = gpu
                continue
            if (gpu.clock_mhz is not None) and (prev.clock_mhz is None):
                by_key[key] = gpu
            elif gpu.vendor == "nvidia" and prev.vendor != "nvidia":
                by_key[key] = gpu
    return list(by_key.values())

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

def read_memory_windows() -> Optional[MemorySample]:
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        if not ok:
            return None
        commit_limit = int(stat.ullTotalPageFile)
        commit_used = int(stat.ullTotalPageFile - stat.ullAvailPageFile)
        return MemorySample(
            total_bytes=int(stat.ullTotalPhys),
            avail_bytes=int(stat.ullAvailPhys),
            commit_used_bytes=commit_used,
            commit_limit_bytes=commit_limit,
            memory_load_pct=float(stat.dwMemoryLoad),
        )
    except Exception:
        return None

def _proc_meminfo() -> Dict[str, int]:
    out: Dict[str, int] = {}
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, rest = line.split(":", 1)
        m = re.search(r"(\d+)", rest)
        if not m:
            continue
        kb = int(m.group(1))
        out[k.strip()] = kb * 1024
    return out

def read_memory_linux() -> Optional[MemorySample]:
    info = _proc_meminfo()
    if "MemTotal" not in info:
        return None
    total = info["MemTotal"]
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    commit_used = info.get("Committed_AS")
    commit_limit = info.get("CommitLimit")
    load = None
    if total > 0:
        load = 100.0 * (1.0 - avail / float(total))
    return MemorySample(
        total_bytes=total,
        avail_bytes=avail,
        commit_used_bytes=commit_used,
        commit_limit_bytes=commit_limit,
        memory_load_pct=load,
    )

def read_memory() -> Optional[MemorySample]:
    if os.name == "nt":
        return read_memory_windows()
    return read_memory_linux()

def _base_sim_memory(
    total_gb: float = 16.0,
    avail_gb: float = 9.0,
    commit_ratio: float = 0.45,
) -> MemorySample:
    total = int(total_gb * 1024 ** 3)
    avail = int(avail_gb * 1024 ** 3)
    limit = int(total * 1.5)
    used = int(limit * commit_ratio)
    return MemorySample(
        total_bytes=total,
        avail_bytes=avail,
        commit_used_bytes=used,
        commit_limit_bytes=limit,
        memory_load_pct=100.0 * (1.0 - avail / float(total)),
    )

def make_sample(
    *,
    rtx_util: float = 55.0,
    rtx_clock: float = 1620.0,
    rtx_max_clock: float = 1740.0,
    rtx_power: float = 78.0,
    rtx_pl: float = 100.0,
    rtx_temp: float = 72.0,
    rtx_present: bool = True,
    rtx_name: str = "NVIDIA GeForce RTX 5060 Laptop GPU",
    rtx_vendor: str = "nvidia",
    rtx_vram_used: float = 3200.0,
    igpu_present: bool = True,
    igpu_name: str = "AMD Radeon 780M Graphics",
    igpu_vendor: str = "amd",
    igpu_util: float = 12.0,
    igpu_temp: float = 62.0,
    igpu_vram_used: float = 256.0,
    igpu_clock: Optional[float] = None,
    igpu_max_clock: Optional[float] = 2800.0,
    mem: Optional[MemorySample] = None,
    assist_rss: int = 0,
    assist_alive: bool = False,
    assist_fail: bool = False,
    ram_at_assist: Optional[int] = None,
    simulated: bool = True,
    ts: Optional[float] = None,
    profile: str = "auto",
    power_source: str = "unknown",
    hot_procs: Optional[Sequence[ProcInfo]] = None,
) -> SystemSample:
    return SystemSample(
        ts=time.time() if ts is None else ts,
        rtx=GpuSample(
            name=rtx_name,
            vendor=rtx_vendor,
            util_pct=rtx_util,
            clock_mhz=rtx_clock,
            max_clock_mhz=rtx_max_clock,
            power_w=rtx_power,
            power_limit_w=rtx_pl,
            temp_c=rtx_temp,
            vram_used_mb=rtx_vram_used,
            vram_total_mb=8188.0,
            present=rtx_present,
            vendor_id=vendor_id_of(rtx_vendor),
            discrete=not looks_integrated(rtx_name, 8188.0),
        ),
        igpu=GpuSample(
            name=igpu_name,
            vendor=igpu_vendor,
            util_pct=igpu_util,
            clock_mhz=igpu_clock,
            max_clock_mhz=igpu_max_clock,
            temp_c=igpu_temp,
            vram_used_mb=igpu_vram_used,
            vram_total_mb=2048.0,
            present=igpu_present,
            vendor_id=vendor_id_of(igpu_vendor),
            discrete=not looks_integrated(igpu_name, 2048.0),
        ),
        memory=mem or _base_sim_memory(),
        assist_rss_bytes=assist_rss,
        assist_alive=assist_alive,
        assist_tdr_or_fail=assist_fail,
        ram_avail_at_assist_start=ram_at_assist,
        simulated=simulated,
        source="sim",
        profile=profile,
        power_source=power_source,
        hot_procs=list(hot_procs or ()),
    )

def scenario_boost() -> SystemSample:
    return make_sample()

def make_proc(
    name: str,
    *,
    pid: int = 4242,
    cpu: float = 0.0,
    gpu: float = 0.0,
    exe: str = "",
    kind: Optional[str] = None,
) -> ProcInfo:
    return ProcInfo(
        pid=pid,
        name=name,
        cpu_pct=cpu,
        gpu_pct=gpu,
        kind=kind or classify_proc_kind(name, exe),
        exe_path=exe,
    )

def scenario_idle() -> SystemSample:
    return make_sample(
        rtx_util=4.0,
        rtx_clock=180.0,
        rtx_power=7.0,
        igpu_util=3.0,
        hot_procs=[],
    )

def scenario_game_boost() -> SystemSample:
    return make_sample(
        rtx_util=72.0,
        rtx_clock=2500.0,
        rtx_max_clock=3090.0,
        rtx_power=80.0,
        hot_procs=[
            make_proc(
                "eldenring.exe",
                pid=2201,
                cpu=41.0,
                gpu=86.0,
                exe=r"C:\Games\eldenring.exe",
            )
        ],
    )

def scenario_backup_clock_drop() -> SystemSample:
    return make_sample(
        rtx_util=88.0,
        rtx_clock=780.0,
        rtx_max_clock=1740.0,
        rtx_power=52.0,
        rtx_pl=100.0,
        rtx_temp=81.0,
        igpu_util=8.0,
    )

def scenario_backup_power_limit() -> SystemSample:
    return make_sample(
        rtx_util=76.0,
        rtx_clock=1500.0,
        rtx_pl=55.0,
        rtx_power=54.0,
        rtx_temp=79.0,
    )

def scenario_backup_util_drop() -> SystemSample:
    return make_sample(
        rtx_util=22.0,
        rtx_clock=400.0,
        rtx_power=12.0,
        hot_procs=[
            make_proc(
                "eldenring.exe",
                pid=7,
                cpu=40.0,
                gpu=22.0,
                exe=r"D:\eldenring.exe",
            )
        ],
    )

def scenario_pause_low_ram() -> SystemSample:
    mem = _base_sim_memory(total_gb=16.0, avail_gb=1.2, commit_ratio=0.55)
    return make_sample(mem=mem)

def scenario_pause_commit() -> SystemSample:
    mem = _base_sim_memory(total_gb=16.0, avail_gb=6.0, commit_ratio=0.94)
    return make_sample(mem=mem)

def scenario_pause_hot() -> SystemSample:
    return make_sample(rtx_temp=93.0, igpu_temp=86.0)

def scenario_amd_boost() -> SystemSample:
    return make_sample(
        rtx_present=False,
        igpu_util=86.0,
        igpu_clock=2550.0,
        igpu_max_clock=2800.0,
        igpu_temp=64.0,
        igpu_vram_used=700.0,
        profile="amd_igpu",
        power_source="ac",
    )

def scenario_amd_throttle() -> SystemSample:
    return make_sample(
        rtx_present=False,
        igpu_util=97.0,
        igpu_clock=1180.0,
        igpu_max_clock=2800.0,
        igpu_temp=88.0,
        igpu_vram_used=1400.0,
        profile="amd_igpu",
        power_source="ac",
    )

def scenario_amd_util_drop() -> SystemSample:
    return make_sample(
        rtx_present=False,
        igpu_util=28.0,
        igpu_clock=900.0,
        igpu_max_clock=2800.0,
        igpu_temp=70.0,
        profile="amd_igpu",
    )

def scenario_amd_ram() -> SystemSample:
    mem = _base_sim_memory(total_gb=16.0, avail_gb=1.1, commit_ratio=0.92)
    return make_sample(
        rtx_present=False,
        igpu_util=80.0,
        igpu_clock=2400.0,
        igpu_vram_used=1800.0,
        mem=mem,
        profile="amd_igpu",
    )

def scenario_amd_battery() -> SystemSample:
    return make_sample(
        rtx_present=False,
        igpu_util=70.0,
        igpu_clock=1600.0,
        igpu_max_clock=2800.0,
        profile="amd_igpu",
        power_source="battery",
    )

DEMO_SCENARIOS: List[Tuple[str, SystemSample, Sequence[GpuSample]]] = []

def _build_demo_scenarios() -> List[Tuple[str, SystemSample, Sequence[GpuSample]]]:
    drop_hist = [
        GpuSample(
            name="NVIDIA GeForce RTX 5060 Laptop GPU",
            vendor="nvidia",
            util_pct=92.0,
            clock_mhz=1680.0,
            max_clock_mhz=1740.0,
            present=True,
        )
        for _ in range(5)
    ]
    return [
        ("BOOST (primary healthy)", scenario_boost(), ()),
        ("BOOST (idle watch)", scenario_idle(), ()),
        ("BOOST (game)", scenario_game_boost(), ()),
        ("BACKUP (clock drop)", scenario_backup_clock_drop(), ()),
        ("BACKUP (power limit < TGP)", scenario_backup_power_limit(), ()),
        ("BACKUP (util drop)", scenario_backup_util_drop(), drop_hist),
        ("PAUSE (RAM < 1.5GB)", scenario_pause_low_ram(), ()),
        ("PAUSE (commit pressure)", scenario_pause_commit(), ()),
        ("PAUSE (package hot)", scenario_pause_hot(), ()),
    ]

def _build_amd_demo_scenarios() -> List[Tuple[str, SystemSample, Sequence[GpuSample]]]:
    drop_hist = [
        GpuSample(
            name="AMD Radeon 780M Graphics",
            vendor="amd",
            util_pct=90.0,
            clock_mhz=2500.0,
            max_clock_mhz=2800.0,
            present=True,
        )
        for _ in range(4)
    ]
    return [
        ("AMD BOOST (iGPU healthy)", scenario_amd_boost(), ()),
        ("AMD RECOVER (clock throttle)", scenario_amd_throttle(), ()),
        ("AMD RECOVER (util drop)", scenario_amd_util_drop(), drop_hist),
        ("AMD PAUSE (shared RAM low)", scenario_amd_ram(), ()),
        ("AMD RECOVER (battery)", scenario_amd_battery(), ()),
    ]

def catalog_tail_demos(n: int = 6) -> List[Tuple[str, SystemSample, bool]]:
    if n <= 0 or not GPU_NAME_CATALOG:
        return []
    rows = GPU_NAME_CATALOG[-min(n, len(GPU_NAME_CATALOG)) :]
    demos: List[Tuple[str, SystemSample, bool]] = []
    for name, vendor, integrated in rows:
        if integrated:
            sample = make_sample(
                rtx_present=False,
                igpu_name=name,
                igpu_vendor=vendor,
                igpu_util=40.0,
                igpu_clock=1000.0,
                igpu_max_clock=1200.0,
            )
        else:
            sample = make_sample(rtx_name=name, rtx_vendor=vendor, igpu_present=False)
        demos.append((f"catalog {name}", sample, False))
    return demos

def catalog_pair_demo() -> Optional[Tuple[str, SystemSample, bool]]:
    discretes = [row for row in GPU_NAME_CATALOG if not row[2]]
    igpus = [row for row in GPU_NAME_CATALOG if row[2]]
    if not discretes or not igpus:
        return None
    dname, dv, _ = discretes[-1]
    iname, iv, _ = igpus[-1]
    sample = make_sample(rtx_name=dname, rtx_vendor=dv, igpu_name=iname, igpu_vendor=iv)
    return (f"catalog pair {dname}+{iname}", sample, False)

class SimClock:

    def __init__(self, profile: str = "hybrid") -> None:
        self.t0 = time.time()
        self.period = 50.0
        self.profile = profile

    def elapsed(self) -> float:
        return time.time() - self.t0

    def sample(self, assist_rss: int, assist_alive: bool, ram_at_assist: Optional[int]) -> SystemSample:
        phase = self.elapsed() % self.period
        if self.profile == "amd_igpu":
            if phase < 12:
                s = scenario_amd_boost()
            elif phase < 24:
                s = scenario_amd_throttle()
            elif phase < 36:
                s = scenario_amd_ram()
            else:
                s = scenario_amd_boost()
        else:
            if phase < 12:
                s = scenario_boost()
            elif phase < 24:
                s = scenario_backup_clock_drop()
            elif phase < 36:
                s = scenario_pause_low_ram()
            else:
                s = scenario_boost()
        s.assist_rss_bytes = assist_rss
        s.assist_alive = assist_alive
        s.ram_avail_at_assist_start = ram_at_assist
        s.simulated = True
        s.source = "sim"
        variant = int(self.elapsed() // self.period) % 10
        if variant == 0 and self.profile != "amd_igpu" and phase < 12:
            s.power_source = "battery"
        if self.profile != "amd_igpu":
            if variant == 1:
                s.igpu.name = "Intel UHD Graphics"
                s.igpu.vendor = "intel"
                s.igpu.vendor_id = INTEL_VENDOR_ID
            elif variant == 2:
                s.igpu.present = False
                s.profile = "auto"
            elif variant == 3:
                s.igpu.name = "Apple M2 GPU"
                s.igpu.vendor = "apple"
                s.igpu.vendor_id = APPLE_VENDOR_ID
            elif variant == 4:
                s.igpu.name = "Qualcomm Adreno 740"
                s.igpu.vendor = "qualcomm"
                s.igpu.vendor_id = QUALCOMM_VENDOR_ID
            elif variant == 5:
                s.rtx.name = "AMD Radeon RX 7800 XT"
                s.rtx.vendor = "amd"
                s.rtx.vendor_id = AMD_VENDOR_ID
                s.igpu.name = "AMD Radeon 780M Graphics"
                s.igpu.vendor = "amd"
                s.igpu.vendor_id = AMD_VENDOR_ID
            elif variant == 6:
                s.rtx.name = "NVIDIA GeForce RTX 4090"
                s.rtx.vendor = "nvidia"
                s.rtx.vendor_id = NVIDIA_VENDOR_ID
                s.igpu.name = "AMD Radeon 840M Graphics"
                s.igpu.vendor = "amd"
                s.igpu.vendor_id = AMD_VENDOR_ID
            elif variant == 7:
                s.rtx.name = "NVIDIA L4"
                s.rtx.vendor = "nvidia"
                s.rtx.vendor_id = NVIDIA_VENDOR_ID
                s.igpu.name = "Intel Arc B50"
                s.igpu.vendor = "intel"
                s.igpu.vendor_id = INTEL_VENDOR_ID
                s.igpu.discrete = True
            elif variant == 8:
                s.rtx.name = "NVIDIA GeForce RTX 5060 Laptop GPU"
                s.rtx.vendor = "nvidia"
                s.rtx.vendor_id = NVIDIA_VENDOR_ID
                s.igpu.name = "AMD Radeon 780M Graphics"
                s.igpu.vendor = "amd"
                s.igpu.vendor_id = AMD_VENDOR_ID
            elif variant == 9:
                s.rtx.name = "Moore Threads MTT S80"
                s.rtx.vendor = "moorethreads"
                s.rtx.vendor_id = MOORETHREADS_VENDOR_ID
                s.igpu.name = "Samsung Xclipse 920"
                s.igpu.vendor = "samsung"
                s.igpu.vendor_id = SAMSUNG_VENDOR_ID
        else:
            if variant == 1:
                s.igpu.name = "Intel Arc Graphics"
                s.igpu.vendor = "intel"
                s.igpu.vendor_id = INTEL_VENDOR_ID
            elif variant == 2:
                s.rtx = GpuSample(
                    name="NVIDIA GeForce RTX 4070",
                    vendor="nvidia",
                    present=True,
                    util_pct=s.igpu.util_pct,
                    clock_mhz=s.igpu.clock_mhz,
                    max_clock_mhz=s.igpu.max_clock_mhz or 2500.0,
                    temp_c=s.igpu.temp_c,
                    vram_used_mb=4000.0,
                    vram_total_mb=8192.0,
                    vendor_id=NVIDIA_VENDOR_ID,
                    discrete=True,
                )
                s.igpu.present = False
                s.profile = "auto"
            elif variant == 3:
                s.igpu.name = "Apple M4 GPU"
                s.igpu.vendor = "apple"
                s.igpu.vendor_id = APPLE_VENDOR_ID
            elif variant == 4:
                s.igpu.name = "ARM Mali-G710"
                s.igpu.vendor = "arm"
                s.igpu.vendor_id = ARM_VENDOR_ID
            elif variant == 5:
                s.igpu.name = "Intel UHD Graphics"
                s.igpu.vendor = "intel"
                s.igpu.vendor_id = INTEL_VENDOR_ID
            elif variant == 6:
                s.igpu.name = "Samsung Xclipse 920"
                s.igpu.vendor = "samsung"
                s.igpu.vendor_id = SAMSUNG_VENDOR_ID
            elif variant == 7:
                s.igpu.name = "Broadcom VideoCore VII"
                s.igpu.vendor = "broadcom"
                s.igpu.vendor_id = BROADCOM_VENDOR_ID
            elif variant == 8:
                s.igpu.name = "Moore Threads MTT S70"
                s.igpu.vendor = "moorethreads"
                s.igpu.vendor_id = MOORETHREADS_VENDOR_ID
            elif variant == 9:
                s.igpu.name = "PowerVR B-Series BXE-4-32"
                s.igpu.vendor = "img"
                s.igpu.vendor_id = IMG_VENDOR_ID
        return s

def gpus_detected(rtx: Optional[GpuSample], igpu: Optional[GpuSample]) -> bool:
    rtx_ok = bool(rtx and rtx.present)
    igpu_ok = bool(igpu and igpu.present)
    return rtx_ok or igpu_ok

def read_power_source() -> str:
    if os.name == "nt":
        data = _ps_json(
            "Get-CimInstance Win32_Battery | Select-Object BatteryStatus | ConvertTo-Json -Compress",
            timeout=5.0,
        )
        if not data:
            return "ac"
        rows = data if isinstance(data, list) else [data]
        statuses = []
        for row in rows:
            if isinstance(row, dict) and row.get("BatteryStatus") is not None:
                try:
                    statuses.append(int(row["BatteryStatus"]))
                except (TypeError, ValueError):
                    continue
        if statuses and all(s == 1 for s in statuses):
            return "battery"
        if statuses:
            return "ac"
        return "ac"
    supply = Path("/sys/class/power_supply")
    if supply.is_dir():
        for bat in supply.glob("BAT*"):
            try:
                status = (bat / "status").read_text(encoding="utf-8").strip().lower()
            except OSError:
                continue
            if status == "discharging":
                return "battery"
            if status in {"charging", "full", "not charging"}:
                return "ac"
        for ac in sorted(supply.glob("AC*")) + sorted(supply.glob("ADP*")) + sorted(supply.glob("Mains*")):
            online = ac / "online"
            try:
                if online.is_file() and online.read_text(encoding="utf-8").strip() == "1":
                    return "ac"
            except OSError:
                continue
    return "unknown"

def read_live_sample() -> Tuple[Optional[SystemSample], str]:
    found: List[GpuSample] = []
    found.extend(read_nvidia_gpus())
    if os.name == "nt":
        found = merge_gpus(found, read_windows_gpus())
    else:
        found = merge_gpus(found, read_linux_gpus())
    primary, secondary, profile = assign_roles(found)
    if not primary.present and not secondary.present:
        return None, "no_gpus"
    mem = read_memory()
    if mem is None:
        mem = _base_sim_memory(avail_gb=4.0)
    hot = read_hot_processes()
    adapters = order_display_gpus(found)
    return (
        SystemSample(
            ts=time.time(),
            rtx=primary,
            igpu=secondary,
            memory=mem,
            simulated=False,
            source="live",
            profile=profile,
            power_source=read_power_source(),
            hot_procs=hot,
            adapters=adapters,
        ),
        "live",
    )

class InstanceLock:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (Path(tempfile.gettempdir()) / LOCK_NAME)
        self._fh: Optional[Any] = None

    def _read_pid(self) -> Optional[int]:
        try:
            text = self.path.read_text(encoding="utf-8").strip().split()
            if not text:
                return None
            return int(text[0])
        except (OSError, ValueError):
            return None

    def _close_fh(self) -> None:
        if not self._fh:
            return
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None

    def _try_steal_stale(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            try:
                self.path.unlink()
                return True
            except OSError:
                return False
        if pid == os.getpid():
            return True
        if pid_alive(pid):
            return False
        log(f"warning: reclaiming stale lock pid={pid} ({self.path})")
        try:
            self.path.unlink()
            return True
        except OSError:
            return False

    def acquire(self) -> bool:
        for attempt in range(2):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._fh = open(self.path, "a+", encoding="utf-8")
            except OSError:
                return False
            locked = False
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError:
                        locked = False
                else:
                    import fcntl

                    try:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        locked = False
            except Exception:
                self._close_fh()
                return False
            if locked:
                try:
                    self._fh.seek(0)
                    self._fh.truncate()
                    self._fh.write(str(os.getpid()))
                    self._fh.flush()
                except OSError:
                    pass
                return True
            self._close_fh()
            if attempt == 0 and self._try_steal_stale():
                continue
            return False
        return False

    def release(self) -> None:
        if not self._fh:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        self._close_fh()
        try:
            self.path.unlink(missing_ok=True)
        except TypeError:
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                pass
        except OSError:
            pass

_ASSIST_PS1 = r'''
$ErrorActionPreference = 'Continue'
$mode = $env:HYBRID_MODE
$budgetMB = [int]$env:HYBRID_BUDGET_MB
$stopFile = $env:HYBRID_STOP_FILE
$beatFile = $env:HYBRID_HEARTBEAT
$w = [int]$env:HYBRID_WIDTH
$h = [int]$env:HYBRID_HEIGHT
$ring = [int]$env:HYBRID_RING
$vendor = 0
[uint32]::TryParse($env:HYBRID_VENDOR, [ref]$vendor) | Out-Null
$skipName = $env:HYBRID_SKIP_NAME
try { $Host.UI.RawUI.WindowTitle = 'hybrid-gpu-assist' } catch {}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class HybridWake {
    public const uint D3D11_SDK_VERSION = 7;
    public const int D3D_DRIVER_TYPE_UNKNOWN = 0;
    public const uint D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20;
    public const int DXGI_FORMAT_B8G8R8A8_UNORM = 87;
    public const int D3D11_USAGE_DEFAULT = 0;
    public const int D3D11_USAGE_STAGING = 3;
    public const uint D3D11_BIND_SHADER_RESOURCE = 0x8;
    public const uint D3D11_BIND_RENDER_TARGET = 0x20;
    public const uint D3D11_CPU_ACCESS_READ = 0x20000;
    public const uint D3D11_RESOURCE_MISC_SHARED = 0x2;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct DXGI_ADAPTER_DESC1 {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string Description;
        public uint VendorId;
        public uint DeviceId;
        public uint SubSysId;
        public uint Revision;
        public UIntPtr DedicatedVideoMemory;
        public UIntPtr DedicatedSystemMemory;
        public UIntPtr SharedSystemMemory;
        public long AdapterLuid;
        public uint Flags;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct D3D11_TEXTURE2D_DESC {
        public uint Width;
        public uint Height;
        public uint MipLevels;
        public uint ArraySize;
        public int Format;
        public uint Count;
        public uint Quality;
        public int Usage;
        public uint BindFlags;
        public uint CPUAccessFlags;
        public uint MiscFlags;
    }

    [DllImport("dxgi.dll")]
    public static extern int CreateDXGIFactory1(ref Guid riid, out IntPtr ppFactory);

    [DllImport("d3d11.dll")]
    public static extern int D3D11CreateDevice(
        IntPtr pAdapter,
        int DriverType,
        IntPtr Software,
        uint Flags,
        IntPtr pFeatureLevels,
        uint FeatureLevels,
        uint SDKVersion,
        out IntPtr ppDevice,
        out int pFeatureLevel,
        out IntPtr ppImmediateContext);

    [DllImport("OpenCL.dll", EntryPoint = "clGetPlatformIDs", ExactSpelling = true)]
    public static extern int clGetPlatformIDs(uint num, IntPtr platforms, out uint num_platforms);

    public static IntPtr GetVTableFunc(IntPtr com, int slot) {
        IntPtr vtbl = Marshal.ReadIntPtr(com);
        return Marshal.ReadIntPtr(vtbl, slot * IntPtr.Size);
    }

    public delegate int EnumAdapters1Del(IntPtr factory, uint adapter, out IntPtr ppAdapter);
    public delegate int GetDesc1Del(IntPtr adapter, out DXGI_ADAPTER_DESC1 desc);
    public delegate int ReleaseDel(IntPtr punk);
    public delegate int CreateTexture2DDel(IntPtr dev, ref D3D11_TEXTURE2D_DESC desc, IntPtr init, out IntPtr tex);
    public delegate void CopyResourceDel(IntPtr ctx, IntPtr dst, IntPtr src);
    public delegate void FlushDel(IntPtr ctx);

    public static string WakeD3D(uint vendorWanted, int width, int height, int ring, string stopFile, string beatFile, int budgetMB, string skipName, int copies, int sleepMs, string paceFile) {
        Guid factoryIid = new Guid("770aae78-f26f-4dba-a829-253c83d1b387");
        IntPtr factory;
        int hr = CreateDXGIFactory1(ref factoryIid, out factory);
        if (hr < 0 || factory == IntPtr.Zero) return "dxgi_factory_fail:" + hr.ToString("X");

        EnumAdapters1Del enumAdp = (EnumAdapters1Del)Marshal.GetDelegateForFunctionPointer(
            GetVTableFunc(factory, 12), typeof(EnumAdapters1Del));
        ReleaseDel release = (ReleaseDel)Marshal.GetDelegateForFunctionPointer(
            GetVTableFunc(factory, 2), typeof(ReleaseDel));

        IntPtr chosen = IntPtr.Zero;
        string chosenName = "";
        IntPtr fallback = IntPtr.Zero;
        string fallbackName = "";
        string skip = skipName == null ? "" : skipName.ToLowerInvariant();
        for (uint i = 0; i < 16; i++) {
            IntPtr adp;
            hr = enumAdp(factory, i, out adp);
            if (hr < 0 || adp == IntPtr.Zero) break;
            GetDesc1Del getDesc = (GetDesc1Del)Marshal.GetDelegateForFunctionPointer(
                GetVTableFunc(adp, 10), typeof(GetDesc1Del));
            DXGI_ADAPTER_DESC1 desc;
            hr = getDesc(adp, out desc);
            if (hr >= 0) {
                string nm = desc.Description == null ? "" : desc.Description;
                string nml = nm.ToLowerInvariant();
                bool skipThis = skip.Length > 0 && nml.IndexOf(skip) >= 0;
                bool basic = nml.IndexOf("microsoft basic") >= 0;
                if (!skipThis && !basic && vendorWanted != 0 && desc.VendorId == vendorWanted && chosen == IntPtr.Zero) {
                    chosen = adp;
                    chosenName = nm;
                    continue;
                }
                if (!skipThis && !basic && fallback == IntPtr.Zero) {
                    fallback = adp;
                    fallbackName = nm;
                    continue;
                }
            }
            ReleaseDel relA = (ReleaseDel)Marshal.GetDelegateForFunctionPointer(
                GetVTableFunc(adp, 2), typeof(ReleaseDel));
            relA(adp);
        }
        if (chosen == IntPtr.Zero) {
            chosen = fallback;
            chosenName = fallbackName;
        }
        if (chosen == IntPtr.Zero) {
            release(factory);
            return "no_assist_adapter";
        }

        IntPtr device, ctx;
        int fl;
        hr = D3D11CreateDevice(chosen, D3D_DRIVER_TYPE_UNKNOWN, IntPtr.Zero,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, IntPtr.Zero, 0, D3D11_SDK_VERSION,
            out device, out fl, out ctx);
        if (hr < 0 || device == IntPtr.Zero || ctx == IntPtr.Zero) {
            ReleaseDel relC = (ReleaseDel)Marshal.GetDelegateForFunctionPointer(
                GetVTableFunc(chosen, 2), typeof(ReleaseDel));
            relC(chosen);
            release(factory);
            return "d3d11_device_fail:" + hr.ToString("X");
        }

        CreateTexture2DDel createTex = (CreateTexture2DDel)Marshal.GetDelegateForFunctionPointer(
            GetVTableFunc(device, 5), typeof(CreateTexture2DDel));
        CopyResourceDel copy = (CopyResourceDel)Marshal.GetDelegateForFunctionPointer(
            GetVTableFunc(ctx, 47), typeof(CopyResourceDel));
        FlushDel flush = (FlushDel)Marshal.GetDelegateForFunctionPointer(
            GetVTableFunc(ctx, 111), typeof(FlushDel));

        IntPtr[] gpu = new IntPtr[Math.Max(2, ring)];
        IntPtr[] cpu = new IntPtr[gpu.Length];
        long used = 0;
        long cap = (long)budgetMB * 1024L * 1024L;
        int made = 0;
        for (int i = 0; i < gpu.Length; i++) {
            long frame = (long)width * (long)height * 4L * 2L;
            if (used + frame > cap) break;
            D3D11_TEXTURE2D_DESC d = new D3D11_TEXTURE2D_DESC();
            d.Width = (uint)width; d.Height = (uint)height;
            d.MipLevels = 1; d.ArraySize = 1;
            d.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
            d.Count = 1; d.Quality = 0;
            d.Usage = D3D11_USAGE_DEFAULT;
            d.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
            d.CPUAccessFlags = 0; d.MiscFlags = 0;
            IntPtr t;
            hr = createTex(device, ref d, IntPtr.Zero, out t);
            if (hr < 0 || t == IntPtr.Zero) break;
            gpu[i] = t;
            D3D11_TEXTURE2D_DESC s = d;
            s.Usage = D3D11_USAGE_STAGING;
            s.BindFlags = 0;
            s.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            IntPtr st;
            hr = createTex(device, ref s, IntPtr.Zero, out st);
            if (hr < 0 || st == IntPtr.Zero) break;
            cpu[i] = st;
            used += frame;
            made++;
        }
        if (made < 2) {
            return "texture_alloc_fail:" + chosenName;
        }

        if (copies < 1) copies = 1;
        if (sleepMs < 0) sleepMs = 0;
        int n = 0;
        while (!System.IO.File.Exists(stopFile)) {
            try {
                if (paceFile != null && paceFile.Length > 0 && System.IO.File.Exists(paceFile)) {
                    string[] p = System.IO.File.ReadAllText(paceFile).Split((char[])null, System.StringSplitOptions.RemoveEmptyEntries);
                    if (p.Length >= 1) copies = Math.Max(1, int.Parse(p[0]));
                    if (p.Length >= 2) sleepMs = Math.Max(0, int.Parse(p[1]));
                }
            } catch {}
            for (int c = 0; c < copies; c++) {
                int a = n % made;
                int b = (n + 1) % made;
                copy(ctx, gpu[b], gpu[a]);
                n++;
            }
            copy(ctx, cpu[0], gpu[0]);
            flush(ctx);
            if ((n % 256) == 0) {
                try { System.IO.File.WriteAllText(beatFile, DateTime.UtcNow.ToString("o") + " d3d " + chosenName + " sleep=" + sleepMs); }
                catch {}
            }
            if (sleepMs > 0) System.Threading.Thread.Sleep(sleepMs);
            else System.Threading.Thread.Sleep(0);
        }
        return "stopped:" + chosenName;
    }
}
'@ -ErrorAction SilentlyContinue

function Write-Beat([string]$msg) {
  try { Set-Content -Path $beatFile -Value $msg -Encoding utf8 } catch {}
}

function Invoke-OpenClAssist {
  $code = @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class OclAssist {
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clGetPlatformIDs(uint n, IntPtr[] plats, out uint num);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clGetPlatformInfo(IntPtr p, uint param, UIntPtr sz, byte[] val, IntPtr ret);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clGetDeviceIDs(IntPtr p, ulong type, uint n, IntPtr[] devs, out uint num);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clGetDeviceInfo(IntPtr d, uint param, UIntPtr sz, byte[] val, IntPtr ret);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern IntPtr clCreateContext(IntPtr props, uint n, IntPtr[] devs, IntPtr cb, IntPtr data, out int err);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern IntPtr clCreateCommandQueue(IntPtr ctx, IntPtr dev, ulong props, out int err);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern IntPtr clCreateBuffer(IntPtr ctx, ulong flags, IntPtr size, IntPtr host, out int err);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clEnqueueCopyBuffer(IntPtr q, IntPtr src, IntPtr dst, IntPtr srcOff, IntPtr dstOff, IntPtr cb, uint n, IntPtr wait, IntPtr ev);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clFinish(IntPtr q);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clReleaseMemObject(IntPtr m);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clReleaseCommandQueue(IntPtr q);
    [DllImport("OpenCL.dll", CallingConvention = CallingConvention.StdCall)]
    public static extern int clReleaseContext(IntPtr c);

    static string Info(Func<byte[], int> getter) {
        byte[] buf = new byte[512];
        getter(buf);
        int z = Array.IndexOf(buf, (byte)0);
        if (z < 0) z = buf.Length;
        return Encoding.ASCII.GetString(buf, 0, z);
    }

    public static string Run(int budgetMB, string stopFile, string beatFile) {
        uint np;
        int err = clGetPlatformIDs(0, null, out np);
        if (err != 0 || np == 0) return "no_opencl_platform";
        IntPtr[] plats = new IntPtr[np];
        clGetPlatformIDs(np, plats, out np);
        IntPtr plat = IntPtr.Zero;
        IntPtr dev = IntPtr.Zero;
        string dname = "";
        for (int i = 0; i < plats.Length; i++) {
            string pname = Info(b => clGetPlatformInfo(plats[i], 0x0902, (UIntPtr)b.Length, b, IntPtr.Zero));
            uint nd;
            if (clGetDeviceIDs(plats[i], 4UL, 0, null, out nd) != 0 || nd == 0) continue; // GPU
            IntPtr[] ds = new IntPtr[nd];
            clGetDeviceIDs(plats[i], 4UL, nd, ds, out nd);
            for (int j = 0; j < ds.Length; j++) {
                string nm = Info(b => clGetDeviceInfo(ds[j], 0x102B, (UIntPtr)b.Length, b, IntPtr.Zero));
                string low = (pname + " " + nm).ToLowerInvariant();
                if (low.Contains("nvidia")) continue;
                if (low.Contains("amd") || low.Contains("radeon") || low.Contains("advanced micro") || low.Contains("gfx")) {
                    plat = plats[i]; dev = ds[j]; dname = nm; break;
                }
            }
            if (dev != IntPtr.Zero) break;
        }
        if (dev == IntPtr.Zero) return "no_amd_opencl_device";
        int e;
        IntPtr[] one = new IntPtr[] { dev };
        IntPtr ctx = clCreateContext(IntPtr.Zero, 1, one, IntPtr.Zero, IntPtr.Zero, out e);
        if (e != 0) return "opencl_context_fail";
        IntPtr q = clCreateCommandQueue(ctx, dev, 0, out e);
        if (e != 0) return "opencl_queue_fail";
        int nbuf = 4;
        int chunk = Math.Max(4, (budgetMB * 1024 * 1024) / nbuf);
        chunk = Math.Min(chunk, 96 * 1024 * 1024);
        IntPtr[] bufs = new IntPtr[nbuf];
        for (int i = 0; i < nbuf; i++) {
            bufs[i] = clCreateBuffer(ctx, 1UL << 0, (IntPtr)chunk, IntPtr.Zero, out e);
            if (e != 0) return "opencl_buffer_fail";
        }
        int n = 0;
        while (!System.IO.File.Exists(stopFile)) {
            for (int c = 0; c < 16; c++) {
                int a = n % nbuf; int b = (n + 1) % nbuf;
                clEnqueueCopyBuffer(q, bufs[a], bufs[b], IntPtr.Zero, IntPtr.Zero, (IntPtr)chunk, 0, IntPtr.Zero, IntPtr.Zero);
                n++;
            }
            clFinish(q);
            if ((n % 128) == 0) {
                try { System.IO.File.WriteAllText(beatFile, DateTime.UtcNow.ToString("o") + " ocl " + dname); } catch {}
            }
            System.Threading.Thread.Sleep(8);
        }
        return "stopped:" + dname;
    }
}
'@
  Add-Type -TypeDefinition $code -ErrorAction Stop
  return [OclAssist]::Run($budgetMB, $stopFile, $beatFile)
}

Write-Beat ("starting mode=" + $mode + " " + $w + "x" + $h + " ring=" + $ring)
$copies = 4
[int]::TryParse($env:HYBRID_COPIES, [ref]$copies) | Out-Null
if ($copies -lt 1) { $copies = 1 }
$sleepMs = 8
[int]::TryParse($env:HYBRID_SLEEP_MS, [ref]$sleepMs) | Out-Null
if ($sleepMs -lt 0) { $sleepMs = 0 }
$paceFile = $env:HYBRID_PACE_FILE
$result = 'uninitialized'
try {
  $result = [HybridWake]::WakeD3D($vendor, $w, $h, $ring, $stopFile, $beatFile, $budgetMB, $skipName, $copies, $sleepMs, $paceFile)
} catch {
  $result = 'd3d_exception:' + $_.Exception.Message
}
if ($result -notmatch '^stopped:') {
  Write-Beat ("d3d_fallback:" + $result)
  try {
    $result = Invoke-OpenClAssist
  } catch {
    $result = 'opencl_exception:' + $_.Exception.Message
    Write-Beat $result
    exit 2
  }
}
Write-Beat $result
exit 0
'''

def _powershell_exe() -> Optional[str]:
    return shutil.which("powershell") or shutil.which("pwsh")

def process_rss_bytes(pid: int) -> int:
    if pid <= 0:
        return 0
    if os.name == "nt":
        script = (
            f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
            "if($p){[int64]$p.WorkingSet64}else{0}"
        )
        exe = _powershell_exe()
        if not exe:
            return 0
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            return int((proc.stdout or "0").strip() or 0)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return 0
    status = Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                kb = int(re.search(r"(\d+)", line).group(1))
                return kb * 1024
    except (OSError, AttributeError, ValueError):
        return 0
    return 0

class IgpuAssist:

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen[str]] = None
        self.stop_path: Optional[Path] = None
        self.beat_path: Optional[Path] = None
        self.script_path: Optional[Path] = None
        self.mode: Optional[str] = None
        self.budget_bytes: int = 0
        self.copies: int = 0
        self.sleep_ms: int = 0
        self.geom: Tuple[int, int, int] = (0, 0, 0)
        self.pace_path: Optional[Path] = None
        self.last_fail: bool = False
        self.work_dir = Path(tempfile.gettempdir()) / "hybrid_gpu_assist"
        self.ram_at_start: Optional[int] = None

    @property
    def alive(self) -> bool:
        return bool(self.proc) and self.proc.poll() is None

    def rss(self) -> int:
        if not self.alive or not self.proc:
            return 0
        return process_rss_bytes(self.proc.pid)

    def failed(self) -> bool:
        if self.last_fail:
            return True
        if self.proc is None:
            return False
        code = self.proc.poll()
        if code is None:
            return False
        if code != 0:
            self.last_fail = True
            return True
        return False

    def _write_stop(self) -> None:
        if self.stop_path:
            try:
                self.stop_path.write_text("stop", encoding="utf-8")
            except OSError:
                pass

    def stop(self, reason: str = "") -> None:
        running = bool(self.mode) or bool(self.proc and self.proc.poll() is None)
        if not running and not self.stop_path:
            return
        self._write_stop()
        proc = self.proc
        self.proc = None
        self.mode = None
        self.budget_bytes = 0
        self.copies = 0
        self.sleep_ms = 0
        self.geom = (0, 0, 0)
        self.pace_path = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        self.ram_at_start = None
        self.stop_path = None
        if reason and running:
            log(f"iGPU worker stopped — {reason}")

    def write_pace(self, copies: int, sleep_ms: int) -> None:
        self.copies = copies
        self.sleep_ms = sleep_ms
        if not self.pace_path:
            return
        try:
            self.pace_path.write_text(f"{max(1, copies)} {max(0, sleep_ms)}\n", encoding="utf-8")
        except OSError:
            pass

    def start(
        self,
        decision: PolicyDecision,
        ram_avail: int,
        simulated: bool,
        secondary: Optional[GpuSample] = None,
        primary_name: str = "",
        silent: bool = False,
    ) -> None:
        sig = (
            decision.mode,
            decision.budget_bytes,
            decision.width,
            decision.height,
            decision.ring,
        )
        if simulated or os.name != "nt":
            self.mode = decision.mode
            self.budget_bytes = decision.budget_bytes
            self.copies = decision.copies
            self.sleep_ms = decision.sleep_ms
            self.geom = (decision.width, decision.height, decision.ring)
            self.last_fail = False
            self.ram_at_start = ram_avail
            return
        if self.alive and (
            self.mode,
            self.budget_bytes,
            self.geom[0],
            self.geom[1],
            self.geom[2],
        ) == sig:
            self.write_pace(decision.copies, decision.sleep_ms)
            return
        self.stop()
        self.last_fail = False
        exe = _powershell_exe()
        if not exe:
            log("warning: no PowerShell, skipping iGPU bind")
            self.last_fail = True
            return
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.stop_path = self.work_dir / "stop.flag"
        self.beat_path = self.work_dir / "heartbeat.txt"
        self.script_path = self.work_dir / "assist.ps1"
        self.pace_path = self.work_dir / "pace.txt"
        try:
            if self.stop_path.exists():
                self.stop_path.unlink()
        except OSError:
            pass
        try:
            self.script_path.write_text(_ASSIST_PS1, encoding="utf-8")
        except OSError as exc:
            log(f"warning: worker script write failed ({exc})")
            self.last_fail = True
            return
        env = os.environ.copy()
        env["HYBRID_MODE"] = decision.mode
        env["HYBRID_BUDGET_MB"] = str(max(1, decision.budget_bytes // (1024 * 1024)))
        env["HYBRID_STOP_FILE"] = str(self.stop_path)
        env["HYBRID_HEARTBEAT"] = str(self.beat_path)
        env["HYBRID_WIDTH"] = str(decision.width)
        env["HYBRID_HEIGHT"] = str(decision.height)
        env["HYBRID_RING"] = str(decision.ring)
        env["HYBRID_COPIES"] = str(max(1, decision.copies or 4))
        env["HYBRID_SLEEP_MS"] = str(max(0, decision.sleep_ms))
        env["HYBRID_PACE_FILE"] = str(self.pace_path)
        vid = 0
        if secondary is not None and secondary.present:
            vid = secondary.vendor_id or vendor_id_of(secondary.vendor)
        env["HYBRID_VENDOR"] = str(vid)
        env["HYBRID_SKIP_NAME"] = primary_name
        self.write_pace(decision.copies, decision.sleep_ms)
        try:
            self.proc = subprocess.Popen(
                [
                    exe,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script_path),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            log(f"warning: iGPU worker start failed ({exc})")
            self.last_fail = True
            return
        self.mode = decision.mode
        self.budget_bytes = decision.budget_bytes
        self.copies = decision.copies
        self.sleep_ms = decision.sleep_ms
        self.geom = (decision.width, decision.height, decision.ring)
        self.ram_at_start = ram_avail
        if not silent:
            log(
                f"iGPU worker start — vendor=0x{vid:04X} skip={primary_name!r} "
                f"{decision.width}x{decision.height} ring={decision.ring} "
                f"copies={decision.copies} sleep={decision.sleep_ms}ms "
                f"budget {fmt_bytes(decision.budget_bytes)} ({decision.mode})"
            )

def describe_mode(decision: PolicyDecision) -> str:
    if decision.profile == "amd_igpu":
        if decision.mode == "BOOST":
            return (
                f"mode BOOST — solo GPU optimize ({decision.amd_perf}). "
                "No dummy blit on the same adapter."
            )
        if decision.mode == "RECOVER":
            return (
                f"mode RECOVER — solo GPU weak ({decision.reason}). "
                "Opening shared RAM/GTT and dropping perf to auto."
            )
        left = int(round(decision.pause_seconds_left))
        why = ",".join(decision.safety_reasons) or decision.reason
        return (
            f"mode PAUSE — solo GPU boost stopped ({why}). "
            f"{left}s left"
        )
    if decision.mode == "BACKUP":
        who = decision.target_name or decision.target_exe or "3D"
        return (
            f"mode BACKUP — primary GPU weak ({decision.rtx_weak_reason}). "
            f"{decision.workload_kind} '{who}' is backed up immediately by the secondary GPU "
            f"[{decision.width}x{decision.height} ring={decision.ring} / {fmt_bytes(decision.budget_bytes)}]"
        )
    if decision.mode == "BOOST":
        who = decision.target_name or decision.target_exe
        if decision.assist_level == "watch" and decision.workload_kind == "idle":
            return "mode BOOST — idle watch. Primary GPU waits with no dummy blit"
        if decision.workload_kind == "video":
            label = who or "player"
            return (
                f"mode BOOST — video process ({label}). "
                "Decode on iGPU; leave the dGPU alone"
            )
        if decision.rtx_boost:
            label = f"'{who}' " if who else ""
            if decision.reason == "misplaced_on_igpu" or decision.assist_level == "misplaced":
                return (
                    "misplaced — iGPU is drawing a GPU-heavy app and RTX is under 50%. "
                    "Rewriting GpuPreference=high performance and relaunching Webots/Valorant/Roblox "
                    "onto RTX with the same command line. iGPU assist is off"
                )
            if decision.assist_level == "nvidia_first":
                return (
                    f"mode BOOST — {decision.workload_kind} {label}. "
                    "Open NVIDIA to 50%+ first. "
                    "iGPU blit is off. High 780M with low RTX is the wrong state"
                )
            if not decision.on_primary:
                extra = (
                    " Relaunching onto RTX with the same command line."
                    if decision.relaunch_game
                    else " Fully quit and relaunch the game to attach it to RTX."
                )
                return (
                    f"mode BOOST — {decision.workload_kind} {label} is not on NVIDIA. "
                    f"Rewriting GpuPreference=high performance.{extra} iGPU assist stays off"
                )
            if decision.assist_level in {"fpsmax", "remainder", "nvidia_first", "climb", "hold", "ease"}:
                extra = ""
                if decision.assist_level == "fpsmax" or decision.reason == "roblox_fps_500":
                    extra = f" Roblox FPS cap {ROBLOX_TARGET_FPS}."
                if decision.igpu_assist:
                    if decision.assist_level == "ease":
                        assist = (
                            f" easing iGPU assist (copies={decision.copies})."
                        )
                    elif decision.assist_level == "climb":
                        assist = (
                            f" climbing iGPU assist (copies={decision.copies})."
                        )
                    else:
                        assist = (
                            f" iGPU remainder {int(TARGET_IGPU_MIN)}-{int(TARGET_IGPU_MAX)}%"
                            f" (copies={decision.copies})."
                        )
                else:
                    assist = " iGPU handles browser/compositor only."
                return (
                    f"mode BOOST — {decision.workload_kind} {label}. "
                    f"NVIDIA primary.{extra}{assist}"
                )
            assist = ""
            if decision.igpu_assist:
                assist = (
                    f" + AMD assist {int(TARGET_IGPU_MIN)}-{int(TARGET_IGPU_MAX)}% "
                    f"(sleep={decision.sleep_ms}ms) "
                    f"[{decision.width}x{decision.height} ring={decision.ring} "
                    f"copies={decision.copies} / {fmt_bytes(decision.budget_bytes)}]"
                )
            return (
                f"mode BOOST — {decision.workload_kind} {label}. "
                f"NVIDIA is primary (target {int(TARGET_RTX_UTIL - 5)}-{int(TARGET_RTX_UTIL + 5)}%)"
                f"{assist}"
            )
        if decision.assist_level == "eco":
            return (
                f"mode BOOST — battery eco. Keep primary GPU, halve secondary budget "
                f"[{decision.width}x{decision.height} ring={decision.ring} / {fmt_bytes(decision.budget_bytes)}]"
            )
        return (
            f"mode BOOST — primary healthy. workload={decision.workload_kind} ({who})"
        )
    left = int(round(decision.pause_seconds_left))
    why = ",".join(decision.safety_reasons) or decision.reason
    return (
        f"mode PAUSE — safety interlock ({why}). "
        f"Secondary GPU off, primary only. {left}s left"
    )

def log_telemetry(sample: SystemSample) -> None:
    src = "sim" if sample.simulated else "live"
    r = sample.rtx
    i = sample.igpu
    m = sample.memory
    log(
        f"[{src}] primary={r.name} ({r.vendor}) present={r.present} util={fmt_opt(r.util_pct, '%')} "
        f"clk={fmt_opt(r.clock_mhz, 'MHz')}/{fmt_opt(r.max_clock_mhz, 'MHz')} "
        f"pwr={fmt_opt(r.power_w, 'W', 1)}/{fmt_opt(r.power_limit_w, 'W', 1)} "
        f"temp={fmt_opt(r.temp_c, 'C')} "
        f"vram={fmt_opt(r.vram_used_mb, 'MB')}/{fmt_opt(r.vram_total_mb, 'MB')}"
    )
    log(
        f"[{src}] secondary={i.name} ({i.vendor}) present={i.present} util={fmt_opt(i.util_pct, '%')} "
        f"clk={fmt_opt(i.clock_mhz, 'MHz')} temp={fmt_opt(i.temp_c, 'C')} "
        f"shared={fmt_opt(i.vram_used_mb, 'MB')}"
    )
    commit = ""
    if m.commit_used_bytes is not None and m.commit_limit_bytes is not None:
        commit = f" commit={fmt_bytes(m.commit_used_bytes)}/{fmt_bytes(m.commit_limit_bytes)}"
    log(
        f"[{src}] RAM avail={fmt_bytes(m.avail_bytes)}/{fmt_bytes(m.total_bytes)} "
        f"({m.avail_ratio * 100:.1f}%){commit} power={sample.power_source}"
    )
    if sample.hot_procs:
        top_cpu = max(sample.hot_procs, key=lambda p: p.cpu_pct)
        top_gpu = max(sample.hot_procs, key=lambda p: p.gpu_pct)
        work = infer_workload(sample)
        log(
            f"[{src}] hot process cpu={top_cpu.name} {top_cpu.cpu_pct:.0f}% "
            f"gpu={top_gpu.name} {top_gpu.gpu_pct:.0f}%({top_gpu.kind}) "
            f"-> workload={work.kind} target={work.target.name if work.target else '-'}"
        )
    else:
        work = infer_workload(sample)
        if work.kind != "idle":
            log(f"[{src}] no hot process — workload={work.kind} ({work.reason})")

class Controller:
    def __init__(self, simulated: bool, force_amd: bool = False, live_line: bool = False) -> None:
        self.simulated = simulated
        self.force_amd = force_amd
        self.live_line = live_line
        self.history: List[GpuSample] = []
        self.pause = PauseState()
        self.peak_pl: Optional[float] = None
        self.peak_util: Optional[float] = None
        self.assist = IgpuAssist()
        profile = "amd_igpu" if force_amd else "hybrid"
        self.sim_clock = SimClock(profile=profile)
        self._stop = False
        self.lock = InstanceLock()
        self.guard = RegistryGuard()
        self.apply_locked = False
        self.cycles = 0
        self.misplaced_streak = 0
        self.last_relaunch = 0.0
        self.prev_copies = 0

    def request_stop(self, *_args: Any) -> None:
        self._stop = True
        log("stop signal — killing worker and restoring settings")

    def _remember(self, sample: SystemSample) -> None:
        if resolve_profile(sample, self.force_amd) == "amd_igpu":
            if sample.igpu.util_pct is not None:
                self.peak_util = max(self.peak_util or 0.0, sample.igpu.util_pct)
            self.history.append(sample.igpu)
        else:
            if sample.rtx.power_limit_w:
                self.peak_pl = max(self.peak_pl or 0.0, sample.rtx.power_limit_w)
            if sample.rtx.util_pct is not None:
                self.peak_util = max(self.peak_util or 0.0, sample.rtx.util_pct)
            self.history.append(sample.rtx)
        if len(self.history) > HISTORY_WINDOW:
            self.history = self.history[-HISTORY_WINDOW:]

    def _decorate(self, sample: SystemSample) -> SystemSample:
        if self.force_amd:
            sample.profile = "amd_igpu"
            sample.rtx.present = False
            if sample.igpu.present:
                sample.adapters = [sample.igpu]
            else:
                sample.adapters = [g for g in sample.adapters if g.present][:1]
        sample.assist_alive = self.assist.alive if not self.simulated else bool(self.assist.mode)
        sample.assist_rss_bytes = self.assist.rss() if not self.simulated else (
            self.assist.budget_bytes if self.assist.mode else 0
        )
        sample.assist_tdr_or_fail = self.assist.failed()
        sample.ram_avail_at_assist_start = self.assist.ram_at_start
        return sample

    def snapshot(self) -> SystemSample:
        if self.simulated:
            s = self.sim_clock.sample(
                assist_rss=self.assist.budget_bytes if self.assist.mode else 0,
                assist_alive=bool(self.assist.mode),
                ram_at_assist=self.assist.ram_at_start,
            )
            return self._decorate(s)
        live, _why = read_live_sample()
        if live is None:
            self.simulated = True
            log("no GPU found, switching to sim mode (same as --sim)")
            return self.snapshot()
        return self._decorate(live)

    def apply(self, sample: SystemSample, decision: PolicyDecision, now: float) -> None:
        self.pause = apply_pause_transition(decision, self.pause, now)
        if self.guard.aborted or self.apply_locked:
            self.assist.stop(reason="apply lock")
            return
        try:
            profile = resolve_profile(sample, self.force_amd)
            if profile == "amd_igpu":
                self.assist.stop(reason="solo GPU — no blit on the same adapter")
                notes = apply_amd_perf_level(
                    decision.amd_perf,
                    self.guard,
                    dry_run=self.simulated or os.name != "posix",
                )
                for note in notes[:3]:
                    log(note)
                return
            boost_notes = apply_workload_boost(
                decision,
                sample,
                self.guard,
                dry_run=self.simulated,
            )
            if should_relaunch_misplaced(
                decision,
                streak=self.misplaced_streak,
                last_relaunch=self.last_relaunch,
                now=now,
            ):
                relaunch_notes = relaunch_heavy_onto_dgpu(
                    dry_run=self.simulated,
                    prefer=decision.target_name or decision.target_exe,
                )
                boost_notes.extend(relaunch_notes)
                self.last_relaunch = now
                self.misplaced_streak = 0
            for note in boost_notes[:8]:
                if self.live_line:
                    continue
                log(note)
            if decision.mode == "PAUSE" or not decision.igpu_assist:
                if self.simulated:
                    if self.assist.mode:
                        if not self.live_line:
                            log("sim: secondary GPU stop (PAUSE) — primary path unchanged")
                    self.assist.mode = None
                    self.assist.budget_bytes = 0
                    self.assist.ram_at_start = None
                    self.assist.copies = 0
                else:
                    self.assist.stop(reason="" if self.live_line else "PAUSE — primary GPU only")
                return
            self.assist.start(
                decision,
                ram_avail=sample.memory.avail_bytes,
                simulated=self.simulated,
                secondary=sample.igpu,
                primary_name=sample.rtx.name,
                silent=self.live_line,
            )
            if self.simulated and not self.live_line:
                log(
                    f"sim: secondary GPU ON ({decision.mode}/{decision.assist_level}) "
                    f"— game stays on primary, secondary is scheduled only"
                )
        except ApplyAbort as exc:
            self.apply_locked = True
            self.guard.abort(str(exc))
            self.assist.stop(reason="ApplyAbort")
        except OSError as exc:
            self.apply_locked = True
            self.guard.abort(f"os_error:{exc}")
            self.assist.stop(reason="OSError")

    def cycle(self) -> PolicyDecision:
        sample = self.snapshot()
        self._remember(sample)
        now = time.time()
        decision = evaluate_policy(
            sample,
            history=self.history[:-1],
            pause=self.pause,
            now=now,
            peak_power_limit_w=self.peak_pl,
            peak_util=self.peak_util,
            force_amd=self.force_amd,
            prev_copies=self.prev_copies,
        )
        if not self.live_line:
            log_telemetry(sample)
            log(describe_mode(decision))
            if decision.extra_bytes_est:
                log(f"estimated extra ≈ {fmt_bytes(decision.extra_bytes_est)}")
            if self.guard.aborted:
                log(f"apply locked ({self.guard.abort_reason}) — monitor only")
        if decision.relaunch_game:
            self.misplaced_streak += 1
        else:
            self.misplaced_streak = 0
        self.apply(sample, decision, now)
        if decision.igpu_assist:
            self.prev_copies = decision.copies
        else:
            self.prev_copies = 0
        if self.live_line:
            print_live_status(format_boost_table(sample, decision))
        self.cycles += 1
        write_heartbeat(self.cycles, extra=f"mode={decision.mode} profile={decision.profile}")
        return decision

    def shutdown(self) -> None:
        end_live_status()
        self.assist.stop(reason="process exit")
        if self.guard._pref_original or self.guard.amd_original_perf or self.guard.nvidia_original_pl is not None or self.guard.nvidia_clocks_locked:
            self.guard.restore()
        self.lock.release()

def run_once_demo(force_sim: bool, force_amd: bool = False) -> int:
    live, _why = read_live_sample()
    use_sim = force_sim or live is None
    if force_amd:
        log("solo GPU profile")
    if use_sim:
        log("replaying policy paths in sim")
        scenarios = _build_amd_demo_scenarios() if force_amd else _build_demo_scenarios()
        for title, sample, hist in scenarios:
            d = evaluate_policy(sample, history=hist, now=time.time(), force_amd=force_amd)
            log(f"--- scenario: {title} ---")
            log_telemetry(sample)
            log(describe_mode(d))
        return 0
    assert live is not None
    if force_amd:
        live.profile = "amd_igpu"
        live.rtx.present = False
    log("live one-shot cycle")
    d = evaluate_policy(live, now=time.time(), force_amd=force_amd)
    log_telemetry(live)
    log(describe_mode(d))
    return 0

def run_unit_tests() -> Tuple[bool, str]:
    start = Path(__file__).resolve().parent / "tests"
    env = os.environ.copy()
    env["HYBRID_QUIET"] = "1"
    for key in ("HYBRIDCOVER_SIM", "HYBRIDCOVER_SOLO", "HYBRIDCOVER_HEARTBEAT"):
        env.pop(key, None)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(start), "-q"],
            cwd=str(start.parent),
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"unittest_timeout:{exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out

def next_unittest_lock(ok: bool, apply_locked: bool, abort_reason: str) -> Tuple[bool, str]:
    if not ok:
        return True, "unittest_failed"
    if apply_locked and abort_reason == "unittest_failed":
        return False, ""
    return apply_locked, abort_reason

def run_overnight(force_amd: bool, interval: float) -> int:
    log("overnight loop — Ctrl+C to stop")
    log("on registry/apply errors, restore immediately and lock further writes")
    ctl = Controller(simulated=True, force_amd=force_amd)
    if not ctl.lock.acquire():
        log("error: another instance is already running")
        return 2
    if ctl.guard.load_crash_bundle():
        log("found a previous crash bundle — restoring first")
        ctl.guard.abort("crash_bundle")
    stop = {"flag": False}

    def _stop(*_a: Any) -> None:
        stop["flag"] = True
        ctl.request_stop()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _stop)
        except Exception:
            pass

    round_n = 0
    while not stop["flag"] and not ctl._stop:
        round_n += 1
        write_heartbeat(round_n, extra="overnight")
        log(f"===== overnight round {round_n} =====")
        log(f"policy {POLICY_REVISION} / catalog {len(GPU_NAME_CATALOG)}")
        try:
            ok, output = run_unit_tests()
        except Exception as exec_exc:
            ok = False
            output = f"unittest_crash:{exec_exc}"
            log(traceback.format_exc())
        for line in output.strip().splitlines()[-8:]:
            log("TEST " + line)
        locked, reason = next_unittest_lock(ok, ctl.apply_locked, ctl.guard.abort_reason)
        if not ok:
            ctl.apply_locked = True
            if not ctl.guard.aborted:
                ctl.guard.abort("unittest_failed")
            log("tests failed — apply locked. Loop continues monitor/reeval only.")
        elif locked != ctl.apply_locked or reason != ctl.guard.abort_reason:
            ctl.apply_locked = False
            ctl.guard.aborted = False
            ctl.guard.abort_reason = ""
            ctl.guard.restored = False
            log("tests passed again — unlocking apply")
        for title, sample, hist in _build_demo_scenarios():
            d = evaluate_policy(sample, history=hist, now=time.time(), force_amd=False)
            log(f"{title}: {d.mode} ({d.reason})")
        for title, sample, hist in _build_amd_demo_scenarios():
            d = evaluate_policy(sample, history=hist, now=time.time(), force_amd=True)
            log(f"{title}: {d.mode} ({d.reason})")
        extras = [
            ("NVIDIA+Intel", make_sample(igpu_name="Intel UHD Graphics", igpu_vendor="intel"), False),
            ("NVIDIA only", make_sample(igpu_present=False), False),
            (
                "Intel only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Intel UHD Graphics",
                    igpu_vendor="intel",
                    igpu_util=42.0,
                    igpu_clock=1300.0,
                    igpu_max_clock=1450.0,
                ),
                False,
            ),
            ("RX only", make_sample(rtx_name="AMD Radeon RX 7800 XT", rtx_vendor="amd", igpu_present=False), False),
            (
                "Apple only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Apple M2 GPU",
                    igpu_vendor="apple",
                    igpu_util=55.0,
                    igpu_clock=1200.0,
                    igpu_max_clock=1400.0,
                ),
                False,
            ),
            (
                "Adreno only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Qualcomm Adreno 740",
                    igpu_vendor="qualcomm",
                    igpu_util=33.0,
                    igpu_clock=700.0,
                    igpu_max_clock=800.0,
                ),
                False,
            ),
            (
                "Mali only",
                make_sample(
                    rtx_present=False,
                    igpu_name="ARM Mali-G710",
                    igpu_vendor="arm",
                    igpu_util=40.0,
                    igpu_clock=850.0,
                    igpu_max_clock=900.0,
                ),
                False,
            ),
            ("RX+780M", make_sample(
                    rtx_name="AMD Radeon RX 7600M XT",
                    rtx_vendor="amd",
                    igpu_name="AMD Radeon 780M Graphics",
                    igpu_vendor="amd",
                ), False),
            ("hybrid battery", make_sample(power_source="battery"), False),
            (
                "two NVIDIA",
                make_sample(
                    rtx_name="NVIDIA GeForce RTX 4070",
                    igpu_name="NVIDIA GeForce RTX 3050 Laptop GPU",
                    igpu_vendor="nvidia",
                    igpu_vram_used=1024.0,
                ),
                False,
            ),
            (
                "Lunar Lake only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Intel Arc Graphics 140V",
                    igpu_vendor="intel",
                    igpu_util=38.0,
                    igpu_clock=1850.0,
                    igpu_max_clock=2050.0,
                ),
                False,
            ),
            (
                "RX 9070 only",
                make_sample(
                    rtx_name="AMD Radeon RX 9070 XT",
                    rtx_vendor="amd",
                    igpu_present=False,
                ),
                False,
            ),
            (
                "Snapdragon X Plus only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Qualcomm Snapdragon X Plus Adreno",
                    igpu_vendor="qualcomm",
                    igpu_util=28.0,
                    igpu_clock=900.0,
                    igpu_max_clock=1100.0,
                ),
                False,
            ),
            (
                "Intel Graphics only",
                make_sample(
                    rtx_present=False,
                    igpu_name="Intel Graphics",
                    igpu_vendor="intel",
                    igpu_util=44.0,
                    igpu_clock=1500.0,
                    igpu_max_clock=1700.0,
                ),
                False,
            ),
        ]
        extras.extend(catalog_tail_demos(8))
        pair = catalog_pair_demo()
        if pair:
            extras.append(pair)
        for title, sample, _force in extras:
            d = evaluate_policy(sample, now=time.time())
            log(f"{title}: {d.mode}/{d.profile} assist={d.igpu_assist} ({d.reason})")
        try:
            ctl.cycle()
        except Exception as exc:
            ctl.apply_locked = True
            ctl.guard.abort(f"cycle_exception:{exc}")
            log(traceback.format_exc())
        slept = 0.0
        while slept < max(5.0, interval) and not stop["flag"]:
            write_heartbeat(round_n, extra="sleep")
            time.sleep(0.5)
            slept += 0.5
    ctl.shutdown()
    log(f"overnight stop (round {round_n})")
    return 0

def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="METEOR GPU boosting")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    p.add_argument("--sim", action="store_true", help="force sim telemetry even if GPUs exist")
    p.add_argument("--amd", action="store_true", help="solo GPU mode (compat)")
    p.add_argument("--solo", action="store_true", help="solo optimize even with multiple GPUs")
    p.add_argument("--overnight", action="store_true", help="keep test+eval loop overnight")
    p.add_argument("--catalog", action="store_true", help="print catalog names and count")
    p.add_argument("--yes", action="store_true", help="skip Yes/No GPU and boost confirms")
    p.add_argument("--interval", type=float, default=LOOP_INTERVAL_SEC, help="interval seconds")
    p.add_argument("--version", action="version", version=f"METEOR {POLICY_REVISION}")
    args = p.parse_args(argv)
    if _env_flag("HYBRIDCOVER_SIM"):
        args.sim = True
    if _env_flag("HYBRIDCOVER_SOLO"):
        args.solo = True
        args.amd = True
    return args

def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args = parse_args(argv)
    if args.catalog:
        print(f"{POLICY_REVISION} {len(GPU_NAME_CATALOG)}")
        for name, vendor, integrated in GPU_NAME_CATALOG:
            kind = "iGPU" if integrated else "dGPU"
            print(f"{kind}\t{vendor}\t{name}")
        return 0
    force_amd = bool(args.amd or args.solo)
    watch = not (args.overnight or args.once or args.catalog)
    if not watch:
        if force_amd:
            log("solo GPU optimize")
        else:
            log("hybrid GPU manager — dGPU 50%+ first, then iGPU assist")
        log(f"policy revision {POLICY_REVISION}")
        elevated = windows_is_elevated()
        if elevated is False:
            log("Not running as admin. VS Code terminals usually are not.")
            log("GpuPreference writes HKCU only, so this session still applies.")
            log("Run from admin PowerShell if nvidia-smi power/clock unlock is needed.")

    if args.overnight:
        interval = args.interval if args.interval != LOOP_INTERVAL_SEC else 20.0
        return run_overnight(force_amd=force_amd, interval=interval)

    if args.once:
        return run_once_demo(force_sim=bool(args.sim), force_amd=force_amd)

    live, _why = read_live_sample()
    if live and live.igpu.present and not live.rtx.present:
        force_amd = True
    simulated = bool(args.sim) or live is None

    ctl = Controller(simulated=simulated, force_amd=force_amd, live_line=True)
    if ctl.guard.load_crash_bundle():
        log("found crash-recovery bundle — restoring settings")
        ctl.guard.abort("crash_bundle")
        ctl.apply_locked = True
    if not ctl.lock.acquire():
        log("error: another hybrid_gpu.py instance is running (lock file)")
        return 2

    def _cleanup() -> None:
        ctl.shutdown()

    atexit.register(_cleanup)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, ctl.request_stop)
        except Exception:
            pass

    opening = ctl.snapshot()
    print_meteor_intro(opening)
    auto = bool(args.yes) or os.environ.get("HYBRID_QUIET") == "1" or not stdin_is_interactive()

    def _rescan() -> Optional[SystemSample]:
        if ctl.simulated:
            return ctl.snapshot()
        live_now, _why = read_live_sample()
        return live_now or ctl.snapshot()

    try:
        wizard = run_startup_wizard(opening, auto=auto, rescan=_rescan)
    except KeyboardInterrupt:
        print_quitting()
        ctl.shutdown()
        return 0
    if wizard != "boost":
        print_quitting()
        ctl.shutdown()
        return 0

    interval = max(0.2, float(args.interval))
    while not ctl._stop:
        try:
            ctl.cycle()
        except KeyboardInterrupt:
            end_live_status()
            try:
                leave = ask_exit()
            except KeyboardInterrupt:
                leave = True
            if leave:
                print_quitting()
                break
            continue
        except ApplyAbort as exc:
            ctl.apply_locked = True
            ctl.guard.abort(str(exc))
        except Exception as exc:
            log(f"cycle error: {exc!r}")
            ctl.apply_locked = True
            ctl.guard.abort(f"cycle_exception:{exc}")
        try:
            for _ in range(int(interval * 10)):
                if ctl._stop:
                    break
                time.sleep(0.1)
        except KeyboardInterrupt:
            end_live_status()
            try:
                leave = ask_exit()
            except KeyboardInterrupt:
                leave = True
            if leave:
                print_quitting()
                break
    ctl.shutdown()
    return 0

if __name__ == "__main__":
    sys.exit(main())
