# DSH-Abaqus：让 DeepSeek Harness + Flash 把 Abaqus 仿真做对

这个工作区给 DeepSeek Harness（DSH）里的模型配了四样东西：一套工具、一套知识、一道人工闸门、一个能自己变好的评测闭环。在 DSH 里把工作区选为 `E:\DSH-Abaqus`，它们就自动生效。

## 目录

| 路径 | 是什么 |
|---|---|
| `AGENTS.md` | 每次会话都会读到的工作区说明：流程、硬性规则、工具清单 |
| `.dsh/skills/` | 按需加载的专题知识（脚本、直接写 INP、单元网格、接触、动力、材料单位、岩土与地震、梁壳、后处理、排错、方案） |
| `abq.cmd`、`tools/abq/` | Abaqus 工具箱：运行脚本、体检、求解、读结果、核查、出图、查接口、找官方算例 |
| `tools/preview/` | Abaqus 风格的 INP 预览面板（三维预览 + 参数树 + 体检 + 审查批准） |
| `cases/` | 已验证的案例库（脚本 + 做法要点 + 踩过的坑 + 验证数值） |
| `dsh-plugin/` | DSH 插件：在开了闸门的工作区里拦截绕过审查的求解命令 |
| `eval/` | 评测集、批量评测、失败归因与自动改进脚本、skill 示例的验证脚本 |
| `.abq/config.json` | 本工作区的人工闸门开关 |

## 日常使用

1. 双击 `tools\preview\start_preview.cmd` 启动预览面板。在 DSH 里按 Ctrl+T 打开右侧栏的浏览器标签，输入 `127.0.0.1:8765`，面板就在对话旁边；它自动显示工作区里最新的 INP。想让面板开机自启：运行一次 `tools\preview\autostart_install.cmd`（撤销用 `autostart_remove.cmd`）。
2. 在 DSH 里描述仿真需求。模型按 `AGENTS.md` 的流程走：弄清需求 → 先估算 → 建模得到 INP → **等你审查** → 求解（工具自动给出结果摘要和核查结论）→ 与估算对比 → 先交付后优化 → 汇报。
3. 模型说"请在预览面板审查"时，在面板里看几何、边界、载荷、接触和体检结果，点「批准求解」或「退回修改」（可以对着具体参数写标注）。批准针对的是这一份 INP 的内容，INP 一改就要重审。
4. 不想每次都审：在工作区里运行 `abq.cmd gate off`；恢复用 `abq.cmd gate on`。

## 工具箱 abq

`E:\DSH-Abaqus\abq.cmd -h` 列出全部命令，每个子命令也支持 `-h`。常用的：

```
abq script model.py        运行 CAE 脚本（无界面），新导出的 INP 自动体检
abq check job.inp          静态体检 + 模型摘要（不调用 Abaqus）；自己生成的 INP 先过这一步
abq datacheck job          求解器数据检查
abq run job                求解；失败时给出错误原文、残差位置和针对性提示，成功时给出结果摘要和核查结论
abq odb job.odb            结果摘要：最值及位置、反力合计、能量、频率
abq odb job.odb --set FixEnd --series RF     某个集合上的合计/平均及其时程；--probe x,y,z 按坐标取；--sets 列出集合
abq verify job             单独核查（完成状态、能量比、警告、平衡信息）
abq sweep model.py --set SEED=0.02,0.01      网格收敛/方案比选
abq render job.odb --var S 用 Abaqus 自己出云图；abq render job.inp 出带边界载荷符号的模型图
abq api Tie                本机 Abaqus 2021 脚本接口的真实签名
abq examples --kw "*CONTACT PAIR"            在官方算例库里按关键字/接口/名字检索；abq fetch 取出
abq cases 接触             检索已验证案例库
```

工具对本机做了三点保护和一点提速：

- 启动 Abaqus 前检查系统可用内存，过低就等待而不是硬上（避免挤垮正在跑的其他求解任务）；这个下限写死在代码里，环境变量只能调严。
- 求解器内存上限默认按空闲内存自动取 1.5～8 GB，大模型用 `--memory 32gb` 显式加大。
- skill 目录里的模板会先复制到当前目录再运行，作业文件不会落在 skill 目录里。
- 少于 20000 个节点的模型自动用 1 核求解。本机实测并行作业启动多花约 9 秒、每个增量步多花 0.05～0.1 秒，小模型用多核反而慢几倍；约 4 万个实体单元以上多核才划算（`eval/skill_checks/cpu_bench.py`）。

## 人工闸门是怎么保证的

- `abq run` 在闸门开启时核对 `.abq/review.json`：这份 INP（按内容的 SHA-256）被批准过才求解。
- `abq script` 在闸门开启时拒绝含 `.submit(` 的建模脚本；`abq sweep` 要求基准模型已获批准。
- 装上 `dsh-plugin\abq-gate` 插件后，DSH 直接拒绝模型在该工作区里绕过工具调用 `abaqus job=…` 或 `abaqus cae …`。安装：运行 `dsh-plugin\install.cmd`，然后重启 DSH；卸载：`dsh plugin --profile desktop remove @local/abq-gate`。不装插件时，前两条仍然有效，但模型理论上可以直接调用 abaqus 绕过。
- 回归测试：`py tools\preview\tests\gate_e2e.py`（面板写入审查结论 → 工具读取；需要面板在运行）。

## 评测与自动改进

```
py eval\run_eval.py --variant baseline --suite hard --repeats 2 --parallel 3 --tag base     裸 DSH
py eval\run_eval.py --variant live     --suite hard --repeats 2 --parallel 3 --tag live     当前配置
py eval\run_eval.py --variant v4       --suite hard --repeats 2 --parallel 3 --tag cand     eval\variants\ 下的候选
py eval\report.py base live cand                                                            并排对比
py eval\optimize.py cycle --name cyc1 --suite basic,hard --repeats 2 --parallel 2           自动改进一轮
```

- 评测题在 `eval/tasks/`，分三组：基础组（`t*`、`h*`，11 道）、难题组（`x*`，8 道）、专业组（`d*`，岩土/抗震/混凝土损伤，8 道）。每题有任务书、带解析解或独立数值解的检查脚本、参考解和故意做错的反例。检查脚本自己从结果库取数，不相信模型写的数字。`py eval\validate_tasks.py` 用参考解和反例验证检查脚本本身。
- 每组题都分调优集（tune）和保留集（holdout）。自动改进时，更强的模型只看调优集的失败记录，修改 `AGENTS.md`、skills 和提示表的副本；副本要么在调优集上明显更准，要么不变差且平均耗时至少降 15%，并且保留集不变差、没有题目从全过变成全挂，才会合并进来并提交到 git。每个候选（无论是否采纳）的改动说明和差异都存在 `eval/history/<名字>/`。工具代码、评测题和检查脚本不会被自动修改。
- 自动候选会写错接口：第一轮里强模型给出的两段代码实跑都是错的。所以写进 skill 的代码必须有对应的实跑脚本：`py eval\skill_checks\run_all.py` 跑全部 19 条验证链，`py eval\skill_snippets.py --calls` 核对 skill 里的每个接口名都在跑通的脚本里出现过。改 skill、改工具、升级 Abaqus 之后都应重跑。
- 分析一次运行：`py eval\trace.py <tag> <题目__rN>` 看步骤，`py eval\step.py … 19` 看某一步的完整输入输出，`py eval\timeline.py <tag>` 看时间花在哪（模型思考、求解、读结果），`py eval\step_errors.py <tag…>` 统计脚本报错，`py eval\rescore.py <tag…>` 在检查脚本改动后重新判分。
- 评测进程以低优先级运行，内存紧张时自动等待。

### 评测结果（DeepSeek-V4.1-Flash，推理强度 high）

三种配置跑同一批题：**裸 DSH**（没有 AGENTS.md、skills 和工具箱，模型自己调 abaqus）、历次**增强配置**。每次运行都在全新的独立目录里进行，判分由检查脚本从结果库取数完成。

| 题组 | 配置 | 通过 | 平均每次运行耗时 |
|---|---|---|---|
| 基础组 11 题 | 裸 DSH | 22/22 | 477 s |
| | 指引 v1（流程偏重） | 14/15（接触题超时 1 次） | 511 s |
| | 指引 v2 | 13/13 | 435 s |
| | 指引 v3 | 11/11 | 391 s |
| 难题组 8 题 × 2 遍 | 裸 DSH | 16/16 | 471 s |
| | 指引 v2 | 16/16 | 534 s |
| | 指引 v3 | 16/16 | 446 s |
| | 指引 v4（当前，只跑 1 遍） | 8/8 | 463 s |
| 专业组 8 题 × 2 遍 | 裸 DSH | 14/16 | 720 s |
| | 指引 v3 | 15/16 | 635 s |
| | 指引 v4（当前） | 15/16 | 535 s |

怎么读这些数：

- **通过率上看不出可靠的差别**。基础组和难题组裸 DSH 自己就能全部做对；专业组裸 DSH 14/16、增强配置 15/16，只差 1 次运行（固结题的初始孔压），这个样本量下不能说增强配置更准。弹性地基梁一题三种配置都是 2 次里挂 1 次。差别在下面三处。
- **交付质量**。检查脚本只判数值；`eval\report_quality.py` 另外统计最终汇报里有没有复核所需的内容（六项，每项有/无）：

  | 配置 | 给出与理论/估算的偏差百分比 | 说明核查结论 | 写出已知局限 | 数字不用科学计数法 | 六项平均 |
  |---|---|---|---|---|---|
  | 基础组 + 难题组：裸 DSH（38 次运行） | 39% | 74% | — | 24% | 3.2 |
  | 基础组 + 难题组：指引 v3（27 次运行） | 78% | 96% | 22% | 59% | 4.4 |
  | 专业组：裸 DSH（16 次运行） | 31% | 69% | 12% | 56% | 3.31 |
  | 专业组：指引 v3（16 次运行） | 75% | 100% | 56% | 75% | 4.88 |
  | 专业组：指引 v4（16 次运行） | 81% | 100% | 94% | 100% | 5.75 |
  | 难题组：指引 v4（8 次运行） | 62% | 100% | 100% | 100% | 5.62 |

  同一道双金属片题：裸 DSH 给出挠度和模型描述就结束；增强配置给出理论公式值、0.20% 的偏差、加密一倍后的收敛情况和核查结论。这项统计只看"有没有写"，不判断写得对不对。
- **流程**。人工闸门、求解前估算、结果摘要与核查、内存保护、案例检索，裸 DSH 都没有。闸门的三步流程（停下等审查 → 按退回意见修改 → 批准后求解）单独实测通过。
- **耗时**。v1 比裸 DSH 慢；v2 在基础组快、在难题组慢；v3 两组都不慢于裸 DSH（基础组少 18%，难题组少 5%）；专业组上 v3 比裸 DSH 少 12%，v4 少 26%。v4 在专业组的提速来自新 skill `abaqus-geotech`（固结、不排水强度、粘性边界、CDP 受拉的已验证关键字写法），调优集四题平均 696 s → 477 s；在难题组上 v4 与 v3 相当（463 s 对 446 s）。v3 的提速主要来自一项发现：此前的指引让模型在小模型上用多核求解，这在本机反而慢几倍，占了 v2 总耗时的 26%。v3 的时间构成：模型思考与生成 70%，求解 12%，读结果脚本 5%，其余 13%。

样本量小（每题 1～2 次），单次运行耗时在同一道题上能差两三倍，所以 15% 以内的耗时差别不要当真；通过率的结论是可靠的。

两点要注意：

- v4 新增的岩土内容来自调优集（d01–d04）的题型，保留集里没有同类题，所以它对"没见过的固结/粘性边界/CDP 题"有多大帮助没有被检验；保留集只说明别的题没有因此变差。v4 的耗时降幅 16% 刚过 15% 的采纳线。
- v4 在难题组上"给出与理论值偏差"的比例是 62%（8 次里 5 次），低于 v3 的 88%（16 次里 14 次）；样本小，原因没有查。

本轮（专业组两条基线、v4 评测、难题组回归）共用约 2.27 亿缓存输入 token、303 万未缓存输入 token、319 万输出 token。专业组一条配置跑 2 遍约 4400 万～9300 万缓存输入 token。

## 装到另一台机器上

这套东西是在一台 Windows 10、Abaqus 2021、DeepSeek Harness 0.2.2 的机器上做的，路径写死了几处，换机器要改：

- 工作区放在 `E:\DSH-Abaqus` 最省事。放在别处时，把 `AGENTS.md`、`.dsh/skills/`、`dsh-plugin/` 和 `eval/` 里出现的 `E:\DSH-Abaqus` 换成你的路径（`git grep -lF "E:\DSH-Abaqus"` 列出这些文件）。
- Abaqus 的位置用环境变量指定：`ABAQUS_BAT`（默认 `D:\SIMULIA\Commandsbaqus.bat`）、`ABQ_SAMPLES_ZIP`（官方算例包 `samples.zip` 的位置）。
- DSH 命令行的位置在 `dsh-plugin\install.cmd` 和 `eval\dshrun.py` 里（默认 `D:\DSHesourcesuntime\cliin\dsh.cmd`）。
- 仓库里**不含任何 Abaqus 自带的内容**（官方算例、接口文档都是达索的）。克隆后用你自己的 Abaqus 在本地生成两个索引，否则 `abq api` 和 `abq examples` 会提示索引尚未建立：

  ```
  abq.cmd build-api                          脚本接口签名索引（约几分钟）
  py toolsbquild_samples_index.py        官方算例索引
  py examples\corpusetch_official.py       可选：预览面板测试用到的 37 个官方算例
  ```

- 评测脚本从 `~/.dsh/.credentials.yaml` 读你自己的 DeepSeek API key，仓库里没有任何 key。跑评测很费 token：专业组一种配置跑 2 遍约 4400 万～9300 万缓存输入 token。
- Abaqus 其他版本没有测过；多核阈值、接口签名和 skill 里的实测数字都来自 Abaqus 2021。

## 维护

- 官方算例索引：`py tools\abq\build_samples_index.py`；接口索引：`abq.cmd build-api`（换 Abaqus 版本后重建）。
- 案例库种子：`py eval\make_seed_cases.py`。
- 预览面板的测试在 `tools\preview\tests\`（解析语料 `run_corpus.py`、界面 `ui_check.py`、大模型 `ui_large_check.py`、服务端 `test_server.py`）。实测到 100 万实体单元（解析约 3 秒，页面载入约 7 秒）和 50 万壳单元。
- 所有可变的指引都在 git 里（标签 `guidance-v1`、`guidance-v2`…），改坏了 `git log` / `git revert` 即可回退。

## 许可

本仓库以 GNU 通用公共许可证第 3 版（GPL-3.0）发布，全文见 `LICENSE`。

`tools/preview/static/` 下的 three.js（r160）和 OrbitControls 来自 three.js 项目，版权归 three.js 作者所有，MIT 许可，许可全文见 `tools/preview/static/LICENSE-three.txt`。

Abaqus 是达索系统的注册商标。本项目与达索系统、DeepSeek 均无关联，使用它需要你自己合法取得的 Abaqus 许可。
