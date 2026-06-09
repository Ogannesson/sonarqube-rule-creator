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

### Export and sync profiles

1. Connect to SonarQube.
2. In `Profile export / sync`, choose language and profile.
3. Select `Export rules`.
4. Edit the generated `Profile Rules` CSV/XLSX file.
5. Load the edited file.
6. Choose `Patch` or `Replace`.
7. Select `Sync precheck`.
8. Review planned actions.
9. Select `Apply sync`.

Patch mode only processes table rows. Replace mode also deactivates active server rules that are missing from the table. Use `active=false` to explicitly deactivate a rule.

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

### 导出和同步 Profile

1. 连接 SonarQube。
2. 在“Profile 导出 / 同步”中选择语言和 Profile。
3. 点击“导出规则表”。
4. 编辑生成的 `Profile Rules` CSV/XLSX 文件。
5. 读取编辑后的文件。
6. 选择 `Patch` 或 `Replace`。
7. 点击“同步预检”。
8. 检查计划动作。
9. 点击“执行同步”。

Patch 模式只处理表格中的行。Replace 模式会把服务器上存在但表格中缺失的激活规则计划为停用。使用 `active=false` 可以显式停用规则。
