# SonarQube Profile Creator User Guide

## English

1. Start `SonarQubeProfileCreator.exe`.
2. Enter the SonarQube URL and token.
3. Select `Test connection`.
4. Select a CSV/XLSX rule table.
5. Check the field mapping.
6. Choose a profile strategy.
7. Select `Run precheck`.
8. Fix any red errors in the source table.
9. Select `Apply changes`.
10. Open the report folder and review `report.xlsx`.

The recommended profile strategy is `Extend default`. It creates a new profile that inherits the current default profile for each language and then applies the rules from the table.

## 中文

1. 启动 `SonarQubeProfileCreator.exe`。
2. 输入 SonarQube 地址和 Token。
3. 点击“连接测试”。
4. 选择 CSV/XLSX 规则表。
5. 检查字段映射。
6. 选择 Profile 策略。
7. 点击“运行预检”。
8. 修复红色错误项。
9. 点击“执行写入”。
10. 打开报告目录，查看 `report.xlsx`。

推荐策略是“默认继承”。它会为每种语言创建一个新 Profile，继承当前默认 Profile，再应用表格中的规则。

