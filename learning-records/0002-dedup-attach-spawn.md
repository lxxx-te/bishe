# 0002 — 处理层实战：Attach / Spawn 与阈值 0.90

用户通过真实事件 #132（中国新闻网+澎湃新闻+澎湃热榜三源聚合）理解了 P3 去重核心逻辑：关键词预筛 → 向量余弦 → 阈值 0.90 分叉 Attach/Spawn。确认理解 dedup_one() 三步逻辑（dedup.py:112-141）与 _attach_to_event 的四步副作用（dedup.py:90-109）。

**Evidence**：在演示真实 API 数据（/api/events/132 的 source_count=3、关键词并集）时无歧义；三问测验覆盖"关键词闸门优先于向量"">0.90 严格大于""source_count 含义"三个易错点。

**Implications**：下一课可讲关键词抽取（keywords.py）或向量相似度计算（embed.py/_cosine），也可直接进入检索层（rag/ 管道），视用户选择。