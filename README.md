# Huggingface Model Downloader (hfd.py)

`hfd.py` 是基于 [padeoe](https://gist.github.com/padeoe/697678ab8e528b85a2a7bddafea1fa4f) 的 `hfd.sh` 开发的一个 Python 脚本，用于从 Hugging Face 下载模型或数据集。

## 亮点

- ⏯️ **断点续传**: 可以随时重新运行或使用 Ctrl+C 中断下载。
- 🚀 **多线程下载**: 利用多线程加速下载过程。
- 🚫 **文件排除**: 使用 `--exclude` 或 `--include` 跳过或指定文件，节省时间。
- 🔐 **认证支持**: 对于需要登录的模型，使用 `--hf_username` 和 `--hf_token` 进行认证。
- 🪞 **镜像站支持**: 通过 `--endpoint` 参数或 `HF_ENDPOINT` 环境变量设置镜像站。
- 🌍 **代理支持**: 通过 `HTTPS_PROXY` 环境变量设置代理。
- 📦 **简单依赖**: 仅依赖 `git` 和 `aria2c/wget`。
- 🔁 **自动重试**: 使用 `--max_retries` 来指定一个下载任务的重试次数
- ☑️ **哈希验证**: 使用 `--verify_hash` 来验证本地文件和 `Git LFS` 的哈希值是否一致，如果不一致则重新下载。
- ⏬ **跳过已下载**: 使用 `Git LFS` 自动区分目标文件的下载状态
- 🔄 **浅克隆支持**: 默认使用 `--depth=1` 进行浅克隆，减小下载体积
- 🗑️ **自动清理**: 默认下载完成后移除 `.git` 目录，避免仓库嵌套问题

## 使用方法

首先，下载 `hfd.py` 并确保 `Python`, `aria2c`, `git` 以及 `git-lfs` 已安装。

>Ubuntu 下安装 `aria2c` `git` `git-lfs` 
>```bash
>sudo apt update
>sudo apt install aria2 git git-lfs
>```

### 命令行参数

```sh
python hfd.py -h
```

#### 参数说明

- `repo_id`: Hugging Face 仓库 ID，格式为 `org/repo_name`。
- `--include`: (可选) 指定包含下载的文件模式，支持多个模式。支持目录模式如 `onnx/*`。
- `--exclude`: (可选) 指定排除下载的文件模式，支持多个模式。
- `--hf_username`: (可选) Hugging Face 用户名，用于认证（不是邮箱）。
- `--hf_token`: (可选) Hugging Face 令牌，用于认证。
- `--endpoint`: (可选) 指定Hugging Face镜像站点，默认使用环境变量`HF_ENDPOINT`或`https://huggingface.co`。
- `--tool`: (可选) 下载工具，可以是 `aria2c`（默认）或 `wget`。
- `-x`: (可选) `aria2c` 的下载线程数，默认为 4。
- `--dataset`: (可选) 标志，表示下载数据集。
- `--local_dir`: (可选) 本地存储模型或数据集的目录路径。
- `--max_retries`: (可选) 最大重试次数，默认为 10。
- `--verify_hash`: （可选）启用哈希验证。脚本会检查本地已存在文件的哈希值是否与 LFS 记录匹配，并在本次运行中下载完成后，校验新下载文件的哈希值。
- `--depth-1`: (默认启用) 使用 `--depth=1` 进行浅克隆，只下载最新提交，减小下载体积。
- `--no-depth-1`: (可选) 不使用浅克隆，下载完整的提交历史。
- `--remove_git`: (默认启用) 下载完成后移除 `.git` 目录，避免Git仓库嵌套问题。
- `--no-remove_git`: (可选) 保留 `.git` 目录及相关Git历史。

#### 示例

下载模型：

```bash
python hfd.py bigscience/bloom-560m
```

下载需要登录的模型：

```bash
python hfd.py meta-llama/Llama-2-7b --hf_username YOUR_HF_USERNAME_NOT_EMAIL --hf_token YOUR_HF_TOKEN
```

下载模型并排除某些文件（例如 `.safetensors`）：

```bash
python hfd.py bigscience/bloom-560m --exclude *.safetensors
```

使用 `aria2c` 和多线程下载：

```bash
python hfd.py bigscience/bloom-560m -x 8
```

只下载特定目录中的文件：

```bash
python hfd.py intfloat/e5-base-v2 --include "onnx/*"
```

使用镜像站点：

```bash
python hfd.py intfloat/e5-base-v2 --endpoint https://hf-mirror.com
```

保留完整Git历史：

```bash
python hfd.py intfloat/e5-base-v2 --no-depth-1 --no-remove_git
```

在Git仓库中使用：

脚本会自动检测当前目录是否为Git仓库，并采取以下措施：
1. 使用带时间戳的临时目录名（例如`e5-base-v2_1744471719`）避免名称冲突
2. 默认克隆完成后会删除`.git`目录避免Git仓库嵌套问题

### 输出

下载过程中，将显示文件 URL、下载进度、重试次数等信息

```bash
$ python hfd.py jxu124/OpenX-Embodiment -x 4 --dataset --local_dir /data/open-x-embd-ds

...
[#f142e5 20MiB/717MiB(2%) CN:4 DL:2.0MiB ETA:5m32s]第 1 次尝试失败: 命令失败: aria2c --console-log-level=error --file-allocation=none -x 4 -s 4 -k 1M -c "https://hf-mirror.com/datasets/jxu124/OpenX-Embodiment/resolve/main/kuka/kuka_00106.tar" -d "kuka" -o "kuka_00106.tar"
开始下载 kuka/kuka_00106.tar (第 2 次尝试)
运行命令: aria2c --console-log-level=error --file-allocation=none -x 4 -s 4 -k 1M -c "https://hf-mirror.com/datasets/jxu124/OpenX-Embodiment/resolve/main/kuka/kuka_00106.tar" -d "kuka" -o "kuka_00106.tar"
[#0ee0e2 16KiB/717MiB(0%) CN:4 DL:5.9KiB ETA:34h13m22s]
...

```

### 致谢

感谢 [padeoe](https://gist.github.com/padeoe/697678ab8e528b85a2a7bddafea1fa4f) 提供的 `hfd.sh` 脚本，为 `hfd.py` 的开发提供了基础。