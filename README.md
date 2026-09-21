<h1 align="center"><strong>◢◤ ═══ METEOR ═══ ◢◤</strong></h1>

> State-of-the-art GPU booster

# Introduction

**Meteor** is a GPU booster that allows you to use both dGPUs and iGPUs. Build are Windows-only right now. <br/>
It leads heavy processes onto dGPUs, and iGPUs wait for backup when framerates or simulation speeds go down. <br/>
Meteor helps with not only GPU boosting, but also minimizing performance decreases and frame-drops. <br/>
A computer with only an iGPU are also available for boosting.

# How-to-use

Meteor is a single script in **Python**. Here's how to use and run it.

1. [Download Python](https://www.python.org/downloads/).
2. Unzip the folder from [Releases](https://github.com/EJH-BAE/meteor/releases). (If you check for latest policies, go [here](https://github.com/EJH-BAE/meteor/releases/latest).)
3. Use Administrator CMD or Administrator PowerShell to activate boosting:
```bash
cd YOUR_PATH_TO_FOLDER
python hybrid_gpu.py
# python amd_igpu.py
```
4. Input 'Yes' for the checks.
5. Enjoy GPU boosting

# Environment Requirements

- **OS** : Windows 10, 11
- **Required Programs** : Python 3 (3.14, 3.11, etc.)
- **Launch** : Administrator CMD, Administrator PowerShell

# License

Meteor is licensed by the MIT License.

# Links
- [Python](https://www.python.org/)
- [Releases](https://github.com/EJH-BAE/meteor/releases)
- [How to run CMD as Administrator](https://blog.comodo.com/pc-security/how-to-run-cmd-as-administrator/)
