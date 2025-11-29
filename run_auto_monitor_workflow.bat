@echo off

chcp 65001 >nul
echo ================================================
echo 【南京大学电费监控脚本（GitHub Workflow 模式）】
echo ================================================
echo.

echo 【正在检查环境...】
python tests\test_environment.py

echo.
echo 【运行带包装器的主脚本以便捕获崩溃信息...】
python src\run_workflow_wrapper.py

echo.
echo 【workflow提交到GitHub】

echo.
echo 【脚本运行完成】
