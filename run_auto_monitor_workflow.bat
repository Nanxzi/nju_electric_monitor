@echo off

chcp 65001 >nul
echo ================================================
echo 【南京大学电费监控脚本（GitHub Workflow 模式）】
echo ================================================
echo.

echo 【正在检查环境...】
python tests\test_environment.py

echo.
echo 【正在运行主脚本...】
python src\nju_electric_monitor_workflow.py

echo.
echo 【workflow提交到GitHub】

echo.
echo 【脚本运行完成】
