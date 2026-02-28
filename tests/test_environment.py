import importlib
import sys
import re
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parents[1]

# 检查 packaging 是否安装，否则提示用户
try:
    from packaging.version import parse
except ImportError:
    print("[错误] 未安装 packaging，请先运行: pip install packaging")
    sys.exit(1)

def parse_requirements(file_path):
    pkgs = []
    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            # 匹配包名和版本
            m = re.match(r"([a-zA-Z0-9_\-]+)\s*([=<>!~]+)\s*([\d\.]+)", line)
            if m:
                name, op, ver = m.groups()
                # beautifulsoup4 实际导入名为 bs4，Pillow 为 PIL
                if name.lower() == "beautifulsoup4":
                    import_name = "bs4"
                elif name.lower() == "pillow":
                    import_name = "PIL"
                else:
                    import_name = name
                pkgs.append((import_name, op, ver, name))
    return pkgs

def check_version(pkg, op, required_version, display_name):
    try:
        mod = importlib.import_module(pkg)
        version = getattr(mod, "__version__", None)
        if version is None and pkg == "PIL":
            import PIL
            version = PIL.__version__
        if version is None:
            print(f"[警告] 未能检测 {display_name} 的版本。")
            return True
        v_inst = parse(version)
        v_req = parse(required_version)
        ok = False
        if op == "==":
            ok = v_inst == v_req
        elif op == ">=":
            ok = v_inst >= v_req
        elif op == "<=":
            ok = v_inst <= v_req
        elif op == ">":
            ok = v_inst > v_req
        elif op == "<":
            ok = v_inst < v_req
        else:
            print(f"[警告] 未知的版本要求：{op}，跳过检测。")
            return True
        if ok:
            print(f"[通过] {display_name} 版本：{version}")
            return True
        else:
            print(f"[错误] {display_name} 版本不符，已安装：{version}，需要：{op}{required_version}")
            return False
    except ImportError:
        print(f"[错误] 未安装 {display_name}。")
        return False

def main():
    # 允许通过命令行参数指定 requirements 文件；否则：
    # 若存在 requirements_workflow.txt，则优先检查 workflow 依赖；
    # 否则退回到默认的 requirements.txt。
    if len(sys.argv) > 1:
        req_arg = Path(sys.argv[1])
        if not req_arg.is_absolute():
            req_path = ROOT_DIR / req_arg
        else:
            req_path = req_arg
    else:
        workflow_req = ROOT_DIR / "requirements_workflow.txt"
        default_req = ROOT_DIR / "requirements.txt"
        if workflow_req.exists():
            req_path = workflow_req
        else:
            req_path = default_req

    if not req_path.exists():
        print(f"[错误] 找不到依赖文件: {req_path}")
        sys.exit(1)

    print(f"使用依赖文件: {req_path}")
    pkgs = parse_requirements(req_path)
    all_ok = True
    for pkg, op, ver, display_name in pkgs:
        if not check_version(pkg, op, ver, display_name):
            all_ok = False
    if all_ok:
        print("\n[环境检测通过]")
        sys.exit(0)
    else:
        print("\n[环境检测未通过，请检查上方错误]")
        sys.exit(1)

if __name__ == "__main__":
    main()