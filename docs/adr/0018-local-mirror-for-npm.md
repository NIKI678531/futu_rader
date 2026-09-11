# ADR-0018 — 前端工具链在本地磁盘镜像里跑，不在 P: 网络盘上跑

- **状态**：已接受
- **日期**：2026-09-11
- **相关**：[README.md](../../README.md)、`frontend/vite.env.js`

## 背景

仓库在 `P:\NIKI\futu-radar`，一块 SMB 网络盘。`frontend/vite.env.js` 顶部已经记了
首启慢、HMR 要轮询这两件事。但还有一件更硬的事没记在任何地方：

**`npm install` 在 P: 上装不上。** 不是慢，是失败 —— npm 解包时大量创建符号链接、
重命名、并发写小文件，SMB 在这几件事上的语义与本地 NTFS 不同，装到一半就报
`EPERM`／`EBUSY`／`ENOENT`。于是 `frontend/node_modules` 在 P: 上根本不存在，
`npm run build`／`test`／`six-state`／`diff` 一条都跑不了。

这不是「配置一下就好」的问题：它取决于盘的性质，不取决于仓库。

## 决策

**源码的唯一真源在 P:，工具链在本地磁盘的镜像里跑。**

镜像位置：`%USERPROFILE%\.futu-radar\mirror`（本机即
`C:\Users\nikili\.futu-radar\mirror`）。`node_modules` 只在镜像里存在。

```bash
cd frontend && npm run mirror        # 同步 P: → 本地镜像
cd frontend && npm run mirror:test   # 同步后在镜像里跑 npm test
```

同步脚本是 `frontend/scripts/mirror.mjs`，用 `robocopy /MIR`，只同步源码目录
（`frontend/src`、`frontend/scripts`、`frontend/public`、`backend/**`、`radar_db`、
`design`、以及若干单文件）。**`node_modules` 与 `dist` 不在同步范围内** ——
`/MIR` 会镜像删除，把它们扫进来就等于每次同步都把依赖删掉再重装。

### 三条规矩

1. **只往一个方向同步：P: → 镜像。** 镜像是可丢弃的构建目录，不是工作副本。
   在镜像里改代码，下一次 `npm run mirror` 就抹掉了。
2. **改完源码必须先同步再验证。** 这是最容易踩的一脚：在 P: 上改了 `src/`，
   直接去镜像里跑 `npm test`，跑的是上一版代码，**而且是绿的**。
3. **`npm run mirror` 自己不跑构建。** 同步与验证分开，是为了让「同步失败」和
   「测试失败」在输出里长得不一样。
4. **不要在 P: 上用 `sed -i`（以及任何靠 rename 落盘的就地改写）。** 它的实现是
   「写临时文件 → rename 覆盖原文件」，而 rename 正是 SMB 上语义不同的那几件事之一。
   实测在 P: 上批量跑 `sed -i` 会报 `cannot rename: Permission denied` —— 而此时
   **原文件已经没了**，改动一并消失，不是「没改成」而是「删掉了」。要就地改写就用
   `open(path, 'r+b')` 写完再 `truncate()`，全程不碰 rename。

## 备选方案

**在 P: 上重试 / 换 npm 配置**（`--no-bin-links`、改 cache 位置）。没采纳：
这些只降低失败概率，不消除它；而一次半装成功的 `node_modules` 比装不上更糟 ——
它会让测试以难以解释的方式失败。

**把仓库整个搬到本地盘。** 没采纳：P: 是团队共享位置，仓库放在哪里不是本项目能定的。

**用 junction / `subst` 把 `node_modules` 指到本地。** 没采纳：问题出在 npm
**写入**时的 SMB 语义，而 junction 的解析发生在写入之前 —— 装的过程仍然走 P: 的路径。
而且它是一层不可见的重定向，坏掉的时候没有任何线索指向它。

## 后果

- CI 与其他同事的机器如果不在网络盘上，`frontend/` 里直接 `npm install` 即可，
  镜像是 P: 环境**专用**的绕行，不是常规流程。
- 多了一条「忘了同步」的失败模式。`mirror.mjs` 因此在结束时打印同步了多少个文件，
  而不是静默成功 —— 数字为 0 与同步了 30 个文件，肉眼要能区分。
- `frontend/scripts/real-data-check.mjs` 也在镜像里跑，理由相同。
- 镜像顺带成了一份**离散的最近备份**。规矩 4 那次事故里，P: 上被 `sed -i` 吃掉的三个
  文件（`backend/core/narrative.py`、`backend/providers/sql.py`、
  `frontend/src/screens/productMonitor/Topics.jsx`）就是从镜像里逐字节拷回来的 ——
  因为同步刚跑过、镜像与 P: 当时完全一致。这是**副作用不是保障**：镜像只在
  `npm run mirror` 跑过之后才是最新的，别把它当备份方案用。
