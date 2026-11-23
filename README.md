# DSH-Abaqus：让 DeepSeek Harness + Flash 把 Abaqus 仿真做对

这个工作区给 DeepSeek Harness（DSH）里的模型配了四样东西：一套工具、一套知识、一道人工闸门、一个能自己变好的评测闭环。在 DSH 里把工作区选为 `E:\DSH-Abaqus`，它们就自动生效。

同一套代码也带 ANSYS（MAPDL）版：工具箱 `tools/ans/`，预览面板也认 MAPDL 的 CDB。模型用的工作区单独放在 [DSH-Ansys](https://github.com/Ncight/DSH-Ansys)，见下面「ANSYS（MAPDL）」一节。

## 目录

| 路径 | 是什么 |
|---|---|
| `AGENTS.md` | 每次会话都会读到的工作区说明：流程、硬性规则、工具清单 |
| `.dsh/skills/` | 按需加载的专题知识（脚本、直接写 INP、单元网格、接触、动力、材料单位、岩土与地震、梁壳、后处理、排错、方案） |
| `abq.cmd`、`tools/abq/` | Abaqus 工具箱：运行脚本、体检、求解、读结果、核查、出图、查接口、找官方算例 |
| `tools/preview/` | 预览面板（三维预览 + 参数树 + 体检 + 审查批准）：INP 用 Abaqus 的样子，MAPDL 的 CDB 用 ANSYS 的样子；可以同时看几个工作区 |
| `tools/ans/` | ANSYS（MAPDL）工具箱 ans：建模、体检、求解、取结果、核查；由 DSH-Ansys 工作区的 `ans.cmd` 调用 |
| `cases/` | 已验证的案例库（脚本 + 做法要点 + 踩过的坑 + 验证数值） |
| `dsh-plugin/` | DSH 插件：在开了闸门的工作区里拦截绕过审查的求解命令 |
| `eval/` | 评测集、批量评测、失败归因与自动改进脚本、skill 示例的验证脚本 |
| `.abq/` | 本工作区的状态（不进 git）：`config.json`（人工闸门开关、给其他任务预留的核数和内存、`peer_workspaces` 共用排队的其他工作区、`mapdl_max_cores`）、`review.json` 和 `review/`（批准记录、批准时的文件副本）、`running.json`（在算和排队的作业）、`perf.jsonl`（每个作业的规模、核数、内存和用时） |

## 日常使用

1. 双击 `tools\preview\start_preview.cmd` 启动预览面板。在 DSH 里按 Ctrl+T 打开右侧栏的浏览器标签，输入 `127.0.0.1:8765`，面板就在对话旁边；它自动显示工作区里最新的 INP。想让面板开机自启：运行一次 `tools\preview\autostart_install.cmd`（撤销用 `autostart_remove.cmd`）。
2. 在 DSH 里描述仿真需求。模型按 `AGENTS.md` 的流程走：弄清需求 → 先估算 → 建模得到 INP → **等你审查** → 求解（工具自动给出结果摘要和核查结论）→ 与估算对比 → 先交付后优化 → 汇报。
3. 模型说"请在预览面板审查"时，在面板里看几何、边界、载荷、接触和体检结果，点「批准求解」或「退回修改」（可以对着具体参数写标注）。批准针对的是这一份 INP 的内容，INP 一改就要重审。点「批准求解」会先弹出确认框，列出这份文件和上次批准的版本（或同一文件夹里最近批准的文件）差在哪几行；「对比已批准」随时可以看这份差异。
4. 不想每次都审：在你自己的终端里、在工作区里运行 `abq.cmd gate off`；恢复用 `abq.cmd gate on`。DSH 里的模型运行 `gate off` 会被拒绝。

## 工具箱 abq

`E:\DSH-Abaqus\abq.cmd -h` 列出全部命令，每个子命令也支持 `-h`。常用的：

```
abq script model.py        运行 CAE 脚本（无界面），新导出的 INP 自动体检
abq check job.inp          静态体检 + 模型摘要（不调用 Abaqus）；自己生成的 INP 先过这一步
abq mesh job.inp           网格体检：单元构成、长宽比/角度/雅可比/形状因子、尺寸突变、最差单元在哪、壳的自由边；--abaqus 再用 Abaqus 自带检查复核
abq datacheck job          求解器数据检查
abq run job                求解；核数、内存按本机此刻的余量和模型规模自动定，不设墙钟上限；失败时给出错误原文、残差位置和针对性提示，成功时给出结果摘要和核查结论
abq run job --plan         只看资源分配的决定，不求解
abq run 分支 --oldjob 基础  重启分析：从已经算完的基础作业接着算（基础作业的 INP 带 *Restart, write）
abq status job             进度、占用的核和内存，以及同时在算、在排队的作业
abq odb job.odb            结果摘要：最值及位置、反力合计、能量、频率
abq odb job.odb --set FixEnd --series RF     某个集合上的合计/平均及其时程；--probe x,y,z 按坐标取；--sets 列出集合
abq verify job             单独核查（完成状态、能量比、警告、平衡信息）
abq sweep model.py --set SEED=0.02,0.01      网格收敛/方案比选
abq render job.odb --var S 用 Abaqus 自己出云图；abq render job.inp 出带边界载荷符号的模型图
abq api Tie                本机 Abaqus 2021 脚本接口的真实签名
abq examples --kw "*CONTACT PAIR"            在官方算例库里按关键字/接口/名字检索；abq fetch 取出
abq cases 接触             检索已验证案例库
```

工具对本机做了两点保护：

- 启动 Abaqus 前检查系统可用内存，过低就等待而不是硬上（避免挤垮正在跑的其他求解任务）；这个下限写死在代码里，环境变量只能调严。
- skill 目录里的模板会先复制到当前目录再运行，作业文件不会落在 skill 目录里。

`abq run` 用几个核、多少内存、算多久，由 `tools/abq/resources.py` 按本机此刻的情况定：

- **给其他任务留多少**：`.abq/config.json` 里的 `reserve_cores`、`reserve_mem_gb`。不写就按机器取物理核的四分之一（至少 2 个）、内存的五分之一（至少 4 GB），16 核 62 GB 的机器是 4 核、12 GB。在自己的终端里可以用环境变量 `ABQ_RESERVE_CORES`、`ABQ_RESERVE_MEM_GB` 临时改，跑测试时用得着；DSH 里的模型设了也不起作用。
- **核数**：先按方程数定建议值：6 万以下 1 核，30 万以下 4 核，120 万以下 8 核，再大 12 核。少于 20000 个节点的模型一律 1 核：本机实测并行作业启动多花约 9 秒、每个增量步多花 0.05～0.1 秒，小模型用多核反而慢几倍，约 4 万个实体单元以上多核才划算（`eval/skill_checks/cpu_bench.py`）。实际给的不超过空闲物理核。算空闲核时，其他程序按预留和它们此刻实际用量中较大的扣；正在算的 Abaqus 作业也要扣，登记过的按分给它的核数和实测中较大的扣，没登记的求解进程（手动提交、从 CAE 提交的）按实测扣，求解进程按进程树归到各自的作业。
- **内存**：按 Abaqus 数据检查估算的"不读写磁盘所需内存"再加 10% 给，先跑过 `abq datacheck` 才有这个估算，否则按方程数的经验值。接触对多的模型，数据检查给的估算可能偏大很多：这次的支架数据检查估 19.7 GB，正式运行时 Abaqus 自己估 10.2 GB。能给的上限是可用内存减去预留，再减去正在算的作业还会再要的部分。不够时就给能给的，Abaqus 会读写磁盘，慢一些。
- **同时算和排队**：资源够就几个作业同时算，不够就排队，按提交顺序启动。有别的作业在算时，大作业空出的核不到建议数的一半、或内存不到所需的四成，会先等它们结束（最多 2 小时，之后有多少用多少）。这是因为核数和内存开算后就固定了，用 2 个核算好几个小时，不如等别的作业结束再算。
- **时长**：默认不设墙钟上限。工具盯着 .sta，每 10 分钟报一次进度（第几步、本步完成多少、预计还要多久）。超过 15 分钟没有新增量就报警；平均每个增量用时的 5 倍比 15 分钟长时，按 5 倍算。连续两次报警就停止作业，并说明卡在第几步第几个增量。
- 每个作业结束后在 `.abq/perf.jsonl` 记下规模、核数、内存、用时和迭代次数；上次中断留下的 `.lck` 文件，在没有对应求解进程时自动清掉。
- 人在自己的终端里仍可以用 `--cpus`、`--memory`、`--timeout` 指定；`AGENTS.md` 里要求模型不要指定。

以前核数默认 1、内存最多 8 GB、墙钟上限 3600 秒。实拍一个 23 万节点、带螺栓预紧和接触的模型时，三项都出了问题：试算只用了 1 个核；Abaqus 估算要 10.2 GB 才不读写磁盘，却只给了 8 GB；正式作业算到 1 小时被砍掉。所以改成了现在这样。

## 人工闸门是怎么保证的

- `abq run` 在闸门开启时核对 `.abq/review.json`：这份 INP（按内容的 SHA-256）被批准过才求解。
- `abq script` 在闸门开启时拒绝含 `.submit(` 的建模脚本；`abq sweep` 要求基准模型已获批准。
- 装上 `dsh-plugin\abq-gate` 插件后，DSH 直接拒绝模型在该工作区里绕过工具调用 `abaqus job=…` 或 `abaqus cae …`。安装：运行 `dsh-plugin\install.cmd`，然后重启 DSH；卸载：`dsh plugin --profile desktop remove @local/abq-gate`。不装插件时，前两条仍然有效，但模型理论上可以直接调用 abaqus 绕过。
- 闸门只有人能关。DSH 给模型跑的命令都带着 `DSH_*` 会话环境变量：在这种命令里 `abq gate off` 直接拒绝，环境变量 `ABQ_GATE` 也不起作用；插件还拦截模型改写 `.abq` 下的配置和批准记录（命令和文件工具都拦）。实拍中 Flash 曾以"会话权限是不再询问"为由自己运行 `abq gate off`，所以补了这一条。
- 批准时面板把这份文件存一份副本（`.abq/review/approved/`，大于 300 MB 的不存）。之后文件改了，或者同一文件夹里出现没批准过的新文件（比如照着批准过的工况改出来的新工况），`abq run` 的拒绝提示和面板的确认框都会列出它和批准版本差在哪几行，审查的人一眼就能看出只改了载荷那一行，还是动了别处。
- 重启分析（`abq run 分支 --oldjob 基础`）要求分支和基础作业的 INP 都已批准，基础作业的重启文件齐全。
- 审查栏的按钮放在状态签前面，按钮位置不随状态文字的长短变化。实拍中有一次状态签变长、按钮右移，想点别的按钮却点成了「批准求解」，所以改了这个顺序。
- 回归测试：`py tools\preview\tests\gate_e2e.py`（面板写入审查结论 → 工具读取；需要面板在运行）。

## ANSYS（MAPDL）

做法和 Abaqus 版一样：模型写命令流建模，人在面板里审，批准了才求解。代码都在这个仓库里；模型每次会话要读的东西（`AGENTS.md`、`ans.cmd`、5 份 skill、闸门开关）在 [DSH-Ansys](https://github.com/Ncight/DSH-Ansys)，在 DSH 里把工作区选为 `E:\DSH-Ansys`。在 Ansys 2025 R1 上做和测的。

一道题三个文件，都在作业的文件夹里：

- `<job>.build.mac`：建模命令流（几何、网格、材料、组件、初始的约束和载荷），不能求解；
- `<job>.cdb`：`ans build` 让 MAPDL 用 CDWRITE 导出的模型，人在面板里审的就是它；
- `<job>.solve.dat`：求解段（分析类型、载荷步、对组件加载、SOLVE），只能用白名单里的命令，面板列出每个载荷步的约束和载荷。

工具箱 `tools\ans\ans.py`，`-h` 列出全部命令：

```
ans build job        跑建模命令流，导出 job.cdb 并体检；命令流里求解过就作废
ans check job        不启动 MAPDL：解析 CDB 和求解段，核对白名单，给出模型摘要和体检结果
ans mesh job         网格质量（长宽比、角度、雅可比、最差单元）
ans run job          核对批准记录 → 排队等资源 → 在干净目录里读入 CDB、执行求解段 → 结果摘要和核查结论；--plan 只看分配
ans rst job          最大位移、反力合计、最大等效应力、频率；--comp 组件上的应力和合计；--step N 某个载荷步末；
                     --lin x1,y1,z1,x2,y2,z2 沿评定线做应力线性化（PRSECT：薄膜、薄膜+弯曲、峰值、总应力）
ans verify job       收工前核查：正常结束了吗、结果是不是现在这份 CDB 和求解段算出来的
ans gate status|on|off
```

- 闸门和 Abaqus 版是同一套：批准记录在工作区的 `.abq/review.json`，按 CDB 和求解段两份内容的 SHA-256 记，哪一份改了都要重审；CDB 还必须是最近一次 `ans build` 导出的那一份，手改过的不让算；模型的命令里关不掉闸门。
- 插件 `dsh-plugin\abq-gate` 也管 ANSYS：开了闸门的工作区里，直接调用 `ANSYS251`、`mapdl`、`runwb2` 的命令会被拒绝，只能走 `ans`。
- 面板按文件内容认软件：CDB 换成 ANSYS 的样子（大纲树、约束和载荷用字母标注、图例），INP 还是 Abaqus 的样子。超过 60 MB 的文件不自动解析（环境变量 `ABQ_PREVIEW_MAX_AUTO_MB` 可改），先报出预计要占多少内存，等人点「仍然解析」。
- 面板同时看几个工作区：在 `tools\preview\` 下建 `extra_roots.local.txt`，一行写一个文件夹（比如 `E:\DSH-Ansys`），`start_preview.cmd` 会带上它。
- 和 `abq` 一样，启动 MAPDL 前先看系统可用内存，过低就等待。
- `ans run` 和 `abq run` 用同一套资源分配（`tools/abq/resources.py`，见上面「`abq run` 用几个核…」一节）：
  - 核数按自由度数分档，再不超过 DSH-Ansys 工作区 `.abq/config.json` 里的 `mapdl_max_cores`，也就是许可证允许的核数。不写时取 4，没有 HPC 许可证的 Mechanical 也够用；这台机器写的是 8。
  - 内存由 MAPDL 自己按需分配，工具不加 `-m`，只在开算前看可用内存和预留够不够。
  - 默认不设墙钟上限。看门狗跟的是 MAPDL 的 `<job>.mntr`：每收敛一个子步多一行，太久没有新子步就报警，连续两次停止作业。第一个子步出来之前（读模型、分解大型线性方程组），输出文件还在变就算有进展，判定上限至少 30 分钟。
- 两个工作区的 `.abq/config.json` 用 `peer_workspaces` 互相列出对方，Abaqus 和 MAPDL 的作业就在同一个队里：按提交顺序启动，互相算进对方占用的核和内存。每个工具只写自己工作区的登记表，对方的只读，所以 DSH 的沙箱只放行本工作区写入也没关系。
- 面板对比 ANSYS 作业时，CDB 和求解段分开比。同一个模型的几个工况，差别通常只在求解段里。
- 换机器：MAPDL 的位置用环境变量 `ANS_MAPDL_EXE` 指定（默认 `D:\Program Files\ANSYS Inc\v251\ansys\bin\winx64\ANSYS251.exe`）。中文系统上的 MAPDL 2025 R1 缺语言文件 `fx0.msb`，`ans` 只给自己启动的 MAPDL 把 `CADOE_LIBDIR251` 指到 en-us 语言目录（`tools\ans\mapdl_run.py` 里的 `LANG_FIX`）；别的版本或装在别处要改这一行。插件提示语和 `skill_checks\run_all.py` 里写着 `E:\DSH-Ansys`（`git grep -lF "E:\DSH-Ansys"` 列出）。
- 测试：`tools\ans\tests\` 下的 `test_roundtrip.py`（同一个模型直接求解和走 CDB + 求解段，结果一致）、`test_rst_extras.py`、`test_rst_lin.py`（对厚壁圆筒的 Lamé 解）、`test_ans_gate.py`，都要本机 MAPDL；`py tools\ans\skill_checks\run_all.py` 把 skill 里引用的 14 个例子全部实跑一遍，和手算对。面板的测试：`tools\preview\tests\test_mapdl_model.py`、`test_solve_segment.py`、`test_multiroot.py`。
- 仓库里没有任何 ANSYS 自带的内容；测试用的 CDB 都是用仓库里的命令流在本机生成的。

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
- 自动候选会写错接口：第一轮里强模型给出的两段代码实跑都是错的。所以写进 skill 的代码必须有对应的实跑脚本：`py eval\skill_checks\run_all.py` 跑全部 20 条验证链，`py eval\skill_snippets.py --calls` 核对 skill 里的每个接口名都在跑通的脚本里出现过。改 skill、改工具、升级 Abaqus 之后都应重跑。
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
- Abaqus 的位置用环境变量指定：`ABAQUS_BAT`（默认 `D:\SIMULIA\Commands\abaqus.bat`）、`ABQ_SAMPLES_ZIP`（官方算例包 `samples.zip` 的位置）。
- DSH 命令行的位置在 `dsh-plugin\install.cmd` 和 `eval\dshrun.py` 里（默认 `D:\DSH\resources\runtime\cli\bin\dsh.cmd`）。
- 仓库里**不含任何 Abaqus 自带的内容**（官方算例、接口文档都是达索的）。克隆后用你自己的 Abaqus 在本地生成两个索引，否则 `abq api` 和 `abq examples` 会提示索引尚未建立：

  ```
  abq.cmd build-api                          脚本接口签名索引（约几分钟）
  py tools\abq\build_samples_index.py        官方算例索引
  py examples\corpus\fetch_official.py       可选：预览面板测试用到的 37 个官方算例
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

`tools/preview/static/three.module.js`（three.js r160）来自 three.js 项目，版权归 three.js 作者所有，MIT 许可，许可全文见 `tools/preview/static/LICENSE-three.txt`。面板的转动控制 `ViewControls.js` 是本项目自己写的。

Abaqus 是达索系统的注册商标，ANSYS 和 MAPDL 是 ANSYS, Inc. 的商标。本项目与达索系统、ANSYS, Inc.、DeepSeek 均无关联，使用它需要你自己合法取得的 Abaqus 或 ANSYS 许可。
