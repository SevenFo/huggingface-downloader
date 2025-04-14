# Huggingface Downloader (hfd)

`hfd` 是一个 Python 脚本，用于从 Hugging Face 或 ModelScope 下载模型或数据集，基于 [padeoe](https://gist.github.com/padeoe/697678ab8e528b85a2a7bddafea1fa4f) 的 `hfd.sh` 开发。

## 亮点

- ⏯️ **断点续传**: 可以随时重新运行或使用 Ctrl+C 中断下载。
- 🚀 **多线程下载**: 利用 `aria2c` 多线程加速下载过程。
- 🌐 **多源支持**: 支持从 Hugging Face 和 ModelScope 下载。
- 🚫 **文件过滤**: 使用 `--exclude` 或 `--include` 跳过或指定文件，节省时间。
- 🔐 **认证支持**: 对于需要登录的仓库，使用 `--hf_username` 和 `--hf_token` 进行认证。
- 🪞 **镜像站支持**: 通过 `--endpoint` 参数或 `HF_ENDPOINT`/`MODELSCOPE_ENDPOINT` 环境变量设置镜像站。
- 🌍 **代理支持**: 通过 `HTTPS_PROXY` 环境变量设置代理。
- 📦 **简单依赖**: 仅依赖 `git`, `git-lfs` 和 `aria2c`/`wget`。
- 🔁 **自动重试**: 使用 `--max_retries` 来指定一个下载任务的重试次数。
- ☑️ **哈希验证**: 使用 `--verify_hash` 来验证本地文件和 `Git LFS` 的哈希值是否一致，并在下载后校验新文件。
- ⏬ **跳过已下载**: 使用 `Git LFS` 自动区分目标文件的下载状态。
- 🔄 **浅克隆默认**: 默认使用 `--depth=1` 进行浅克隆，减小下载体积，可通过 `--full_clone` 关闭。
- 💾 **保留 Git 历史默认**: 默认下载完成后保留 `.git` 目录，可通过 `--remove_git` 选择移除。

## 安装

确保你已安装 `Python (>=3.8)`, `git`, `git-lfs` 和 `aria2c` (推荐) 或 `wget`。

> Ubuntu 下安装依赖:
> ```bash
> sudo apt update
> sudo apt install git git-lfs aria2c python3-pip python3-venv
> ```

然后，可以通过 pip 安装本工具：

**从本地源码安装 (开发模式):**

```bash
# 克隆仓库 (如果还没克隆)
# git clone https://github.com/your_username/huggingface-downloader.git
# cd huggingface-downloader

# 安装 (使用 -e 进行可编辑安装)
pip install -e .
```

**或者，通过 TestPyPI:**

```bash
pip install -i https://test.pypi.org/simple/ siky-hfd
```

## 使用方法

安装完成后，可以直接使用 `hfd` 命令。

### 命令行参数

```sh
hfd -h
```

#### 参数说明

- `repo_id`: Hugging Face 或 ModelScope 仓库 ID，格式为 `org/repo_name`。
- `--source`: (可选) 选择下载源平台 (`huggingface` 或 `modelscope`)，默认为 `huggingface`。
- `--include`: (可选) 指定包含下载的文件模式，支持多个模式。支持目录模式如 `onnx/*`。
- `--exclude`: (可选) 指定排除下载的文件模式，支持多个模式。
- `--hf_username`: (可选) Hugging Face 或 ModelScope 用户名，用于认证（不是邮箱）。
- `--hf_token`: (可选) Hugging Face 或 ModelScope 令牌，用于认证。
- `--endpoint`: (可选) 指定源平台的镜像站点 (例如 `https://hf-mirror.com` 或 `https://modelscope.cn/api`)。如果未指定，脚本会尝试使用环境变量 `HF_ENDPOINT` 或 `MODELSCOPE_ENDPOINT`，最后回退到官方地址 (`https://huggingface.co` 或 `https://modelscope.cn`)。
- `--tool`: (可选) 下载工具，可以是 `aria2c`（默认）或 `wget`。
- `-x`, `--threads`: (可选) `aria2c` 的下载线程数，默认为 4。
- `--dataset`: (可选) 标志，表示下载数据集而不是模型。
- `--local_dir`: (可选) 本地存储模型或数据集的目录路径。默认为仓库名称。
- `--max_retries`: (可选) 下载单个文件时的最大重试次数，默认为 10。
- `--verify_hash`: (可选) 启用哈希验证。脚本会检查本地已存在文件的哈希值是否与 LFS 记录匹配，并在本次运行中下载完成后，校验新下载文件的哈希值。
- `--full_clone`: (可选) 执行完整克隆（下载所有 Git 历史记录），而不是默认的浅克隆 (`--depth=1`)。
- `--remove_git`: (可选) 下载完成后移除 `.git` 目录。默认情况下会保留 `.git` 目录。
- `--download_workers`: (可选) 用于并行下载的最大工作线程数 (默认为 8)。
- `--verify_workers`: (可选) 用于并行哈希校验的最大工作线程数 (默认为 4)。

#### 示例

下载 Hugging Face 模型 (默认浅克隆, 保留 .git):

```bash
hfd bigscience/bloom-560m
```

下载 ModelScope 模型:

```bash
hfd modelscope/Llama-2-7b-ms --source modelscope
```

下载需要登录的模型 (Hugging Face):

```bash
hfd meta-llama/Llama-2-7b --hf_username YOUR_HF_USERNAME_NOT_EMAIL --hf_token YOUR_HF_TOKEN
```

下载模型并排除某些文件:

```bash
hfd bigscience/bloom-560m --exclude *.safetensors
```

使用 `aria2c` 和 8 线程下载，增加下载并发数:

```bash
hfd bigscience/bloom-560m --threads 8 --download_workers 16
```

只下载特定目录中的文件:

```bash
hfd intfloat/e5-base-v2 --include "onnx/*"
```

使用镜像站点 (Hugging Face):

```bash
hfd intfloat/e5-base-v2 --endpoint https://hf-mirror.com
```

进行完整克隆并移除 Git 历史:

```bash
hfd intfloat/e5-base-v2 --full_clone --remove_git
```

在Git仓库中使用：

脚本会自动检测当前目录是否为Git仓库，并采取以下措施：
1.  如果未指定 `--local_dir`，则使用带时间戳的临时目录名（例如`e5-base-v2_1744471719`）避免名称冲突。
2.  默认克隆完成后会保留`.git`目录 (可通过 `--remove_git` 移除)。

### 输出

下载过程中，将显示文件 URL、下载进度、重试次数等信息

```bash
$ hfd jxu124/OpenX-Embodiment -x 4 --dataset --local_dir /data/open-x-embd-ds

...
[#f142e5 20MiB/717MiB(2%) CN:4 DL:2.0MiB ETA:5m32s]第 1 次尝试失败: 命令失败: aria2c --console-log-level=error --file-allocation=none -x 4 -s 4 -k 1M -c "https://hf-mirror.com/datasets/jxu124/OpenX-Embodiment/resolve/main/kuka/kuka_00106.tar" -d "kuka" -o "kuka_00106.tar"
开始下载 kuka/kuka_00106.tar (第 2 次尝试)
运行命令: aria2c --console-log-level=error --file-allocation=none -x 4 -s 4 -k 1M -c "https://hf-mirror.com/datasets/jxu124/OpenX-Embodiment/resolve/main/kuka/kuka_00106.tar" -d "kuka" -o "kuka_00106.tar"
[#0ee0e2 16KiB/717MiB(0%) CN:4 DL:5.9KiB ETA:34h13m22s]
...

```

### 致谢

感谢 [padeoe](https://gist.github.com/padeoe/697678ab8e528b85a2a7bddafea1fa4f) 提供的 `hfd.sh` 脚本，为 `hfd.py` 的开发提供了基础。