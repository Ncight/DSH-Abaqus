# CHANGES — 针对 eval `v1_live` / variant `live` 中 t05_hertz_contact 失败的修复

本目录本轮只记录了 1 个失败任务：`t05_hertz_contact`（钢球压刚性平面，要求作业名 `hertz`、输出 result.json 三个量）。

## 1. 根因分析（按检查器判定逐条归类）

物理建模本身是对的：`polar_t1` 已得到最大接触压力 3.7456e9 Pa（期望 3.7211e9，偏差 0.7%），说明模型、单位、加载都对。失败全在**交付环节**：

| 判定 | 根因 | 类别 | 证据 |
|---|---|---|---|
| job_completed（hertz.sta 缺失） | 把预算全花在网格试算上：作业起名 `polar_t1/t2/t3`，任务书要求的 `hertz` 直到最后（第 4 次 run）才提交，会话在 161 步 / 1801 s 结束，作业没算完，ODB 里没有任何场输出 | 缺检查 | 命令统计 run=4、script=33；最后可见命令是 `abq run polar_t3`；最终回答为空 |
| max_contact_pressure / contact_force / frictionless 全部 NOT EVALUATED | hertz.odb 是未完成作业的产物（最后一帧无 CPRESS/RF/U），根因同上 | 缺检查 | 检查器：`no CPRESS field in the last frame (available: )` |
| result_json 缺失 | 在 polar_t1/t2 上已能取到合格数值，却始终没写 result.json（后续把时间花在反复重划网格上） | 缺检查 | 步骤 37 已有 p max 3.7456e9；全程无写 result.json 动作 |
| report_max_contact_pressure_Pa / report_contact_force_N / report_contact_radius_m | 依赖 result.json 的汇报键，因 result.json 不存在而全挂 | 缺检查（连带） | 检查器：`no valid value ... in result.json` |
| （步骤 55–60）两次 `TypeError: arg1; 参数过多` | 该中文报错（英文 takes exactly n arguments）在 hints.json 里没有对应提示，模型改了两轮才想起 `abq api findAt` | 缺工具/工具提示不足 | 步骤 55/58 两次同错，步骤 56 查 api 后修复 |
| （步骤 30）`IndexError: tuple index out of range` | 空对象取下标，无提示；指引中只有"getByBoundingBox 选空不报错"一句话，没有报错对照 | 缺工具/工具提示不足 | 步骤 30 |
| 接触半径提取反复折腾（步骤 36–50 自写 dump_contact.py 多轮） | 指引讲了"先按解析解估接触半宽定网格"，但**从结果里怎么量接触斑半径**没有任何一处说明；postprocess 也没有读 CPRESS 场的写法 | 缺知识 | 步骤 36–50 围绕 CPRESS CSV 的反复读写 |
| 模型能力上限 | 33 次 `abq script` 迭代、大量自写脚本微调，说明弱模型的脚本调试能力有限；步数预算耗尽时最终回答为空 | 模型能力上限 | script=33、outcome None |

未发现"需求理解错"：模型化方式（轴对称、刚性平面、力远离接触区）完全符合任务书。

## 2. 修改清单

| 文件 | 修改 | 针对类别 | 预期效果 |
|---|---|---|---|
| AGENTS.md | 流程新增第 8 步"**先交付，后优化**"：第一次得到与估算量级一致的结果后，先走通"任务书作业名正式求解 → 取数写结果文件 → 汇报草稿"，确认交付物能产出后再继续优化；同类报错改 2 次仍失败就换思路 | 缺检查 | 即使预算耗尽，交付物也已存在；杜绝"试算 3 个作业名、正式作业没跑完" |
| AGENTS.md | 硬性规则"按任务书命名"扩展：试算/比选可用临时作业名，但交付作业必须按任务书作业名重跑，交付物一律取自正式作业 | 缺检查 | 任务书要求的作业名/文件名在交付时必然存在 |
| AGENTS.md | 核查步骤改为逐项核对交付清单（文件、作业名/文件名/键名/单位、result.json 可读回且出自正式作业 ODB）；"完成后回顾"同步加强，并加"没完成如实说明，不要空手结束" | 缺检查 | 汇报前强制自检，消灭"空最终回答" |
| abaqus-contact/SKILL.md | "读结果、做核对"新增：接触斑半径 = 从面上 CPRESS>0 的节点按离接触中心距离排序的最远距离，节点间距即分辨率；峰值压力与接触斑半径都需加密确认 | 缺知识 | 换一道接触题也能直接照着提取接触半径/面积，不再反复自造读取脚本 |
| abaqus-postprocess/SKILL.md | "写结果文件"：只从正式交付作业的 ODB 取数、数字取齐立即写文件、写后读回校验；ODB Python 段新增 CPRESS 标量场读法（v.magnitude/v.data[0]、不在初始帧找、节点坐标排序） | 缺检查 + 缺知识 | result.json 成为交付流程的早段动作；CPRESS 读取有现成写法 |
| abaqus-cae-scripting/SKILL.md | 报错对照表新增两行：`TypeError: arg1; 参数过多`（findAt 只收一个坐标序列）、`IndexError: tuple index out of range`（上一步没生成对象） | 缺工具/工具提示不足 | 同类脚本错误一步定位，少 2–3 轮试错 |
| tools/abq/hints.json | 新增两条提示：`参数过多|应当提供 \d+, 已获得` → findAt 单参数陷阱 + abq api 核对；`IndexError: (tuple|list) index out of range` → 空对象取下标（getByBoundingBox 选空不报错） | 缺工具/工具提示不足 | 报错当场给出修复方向，不必等模型自己想 |

## 3. 指引帮不上的部分（如实说明）

- **脚本调试能力**：33 次 `abq script` 运行、两轮同文案报错才查 api，属于弱模型本身的迭代能力；指引只能缩短每轮，无法消除。
- **预算耗尽**：1801 s / 161 步内没写出最终回答。"先交付后优化"能让中途被打断时交付物仍然完整，但模型若继续深挖网格细节，仍可能把时间花光——这是能力上限与时间管理的残余风险，指引只能缓解不能根除。
- **试算作业与正式作业之间的数值一致性**：指引只能强制"正式作业重跑一遍"，无法保证模型不偷懒；若将来出现"把试算数写进正式交付"，属于作弊级行为，指引防不住。

## 4. 合规说明

- 所有修改均为通用工程知识与工作方法，不含本任务的具体数值、尺寸或答案。
- 未改动 frontmatter 中的 name；hints.json 已校验为合法 JSON（29 条）；AGENTS.md 现为 49 行（≤90）。
- 本次只改了 AGENTS.md、三个 SKILL.md、hints.json，并新建本文件。
