import sys
import pytest
from hfd import cli  # 导入你的 cli 模块


# 这是一个非常基础的示例测试，你需要根据实际功能扩展它
def test_parse_args_basic(mocker):
    """测试基本的参数解析。"""
    # 使用 mocker 来模拟 sys.argv，避免全局修改
    mocker.patch("sys.argv", ["hfd", "org/repo"])
    args = cli.parse_args()
    assert args.repo_id == "org/repo"
    assert args.source == "huggingface"  # 检查默认值
    assert args.threads == 4
    assert not args.full_clone
    assert not args.remove_git
    assert args.download_workers == 8  # 检查新添加参数的默认值
    assert args.verify_workers == 4  # 检查新添加参数的默认值


# 示例：测试 --full_clone
def test_parse_args_full_clone(mocker):
    """测试 --full_clone 标志。"""
    mocker.patch("sys.argv", ["hfd", "org/repo", "--full_clone"])
    args = cli.parse_args()
    assert args.full_clone is True


# 示例：测试 --remove_git
def test_parse_args_remove_git(mocker):
    """测试 --remove_git 标志。"""
    mocker.patch("sys.argv", ["hfd", "org/repo", "--remove_git"])
    args = cli.parse_args()
    assert args.remove_git is True


# 示例：测试 --source modelscope
def test_parse_args_source_modelscope(mocker):
    """测试 --source modelscope。"""
    mocker.patch("sys.argv", ["hfd", "modelscope/repo", "--source", "modelscope"])
    args = cli.parse_args()
    assert args.repo_id == "modelscope/repo"
    assert args.source == "modelscope"


# 示例：测试线程数参数
def test_parse_args_threads(mocker):
    """测试 -x 或 --threads 参数。"""
    mocker.patch("sys.argv", ["hfd", "org/repo", "-x", "16"])
    args = cli.parse_args()
    assert args.threads == 16

    mocker.patch("sys.argv", ["hfd", "org/repo", "--threads", "10"])
    args = cli.parse_args()
    assert args.threads == 10


# 你可以添加更多测试，例如：
# - 测试 --include 和 --exclude 的逻辑
# - 测试 --local_dir
# - 测试 --verify_hash
# - 使用 mocker (pytest-mock) 来模拟 subprocess.run, os.path.exists, shutil.rmtree 等文件和进程操作
# - 测试 core.py 中的函数逻辑（可能需要创建单独的 test_core.py 文件）

# 注意：运行涉及文件系统或网络操作的测试需要小心处理，
# 通常使用 pytest 的 tmp_path fixture 来创建临时目录，并使用 mocker 来模拟外部依赖。
