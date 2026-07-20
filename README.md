# hakoniwa-business-pack

> **A catalog and recipe hub for composing Hakoniwa components with AI to turn user ideas into working demos and products.**

## AI Bootstrap Prompt

If you are an AI agent, do not summarize this repository after reading only this
README.

Before answering that you understand `hakoniwa-business-pack`, read these files
in order:

1. `AGENTS.md`
2. `catalog/index.yaml`
3. `catalog/schema.yaml`
4. `docs/hakoniwa-runtime-primer.md`
5. relevant `catalog/components/*.yaml`
6. `recipes/README.md`
7. relevant `recipes/examples/*.yaml`

Then answer in this shape:

```text
I have read:
- AGENTS.md: yes/no
- catalog/index.yaml: yes/no
- catalog/schema.yaml: yes/no
- docs/hakoniwa-runtime-primer.md: yes/no
- relevant component catalogs: <names or none>
- relevant recipes: <names or none>

Current understanding:
- what this repository is
- what Hakoniwa runtime assumptions matter
- what can be proposed from catalog evidence
- what is feasible, verified, unknown, or blocked
```

Do not jump from "Can Hakoniwa do X?" to adjacent source repositories or local
execution. First use the Catalog and Recipes. If no Recipe exists, propose a
Recipe-shaped plan with `feasibility` and `validation` states. Run build, fetch,
install, launch, GUI, browser, or server commands only after the user asks for
local execution.

For Hakoniwa Python workflows, assume Python 3.12 unless a Recipe explicitly
verifies another version. `hakopy` comes from `hakoniwa-core-pro`; `hakoniwa-pdu`
comes from `pip install hakoniwa-pdu`. Verify both in the same Python 3.12
environment before running SHM/PDU demos.

Before executing a local Hakoniwa Recipe, run the common preflight:

```bash
bash tools/doctor.bash
```

This checks the shared assumptions used by many Recipes: Hakoniwa core install,
`hako-cmd`, Python 3.12, `hakopy`, `hakoniwa_pdu`, and the Python launcher.
The common doctor dispatches to an OS-specific script such as
`tools/doctor-mac.bash`. `tools/docker-mac.bash` is kept as a compatibility
alias for the Mac preflight name discussed during early trials.

## 箱庭で、何をしたいですか？

箱庭には、MuJoCo、Godot、PDU、Endpoint、Conductor、Foxglove など、
シミュレーションを構成するためのさまざまな部品があります。

箱庭をよく知っている人であれば、

> 「これとこれを組み合わせれば、こんなことができそう」

と考えることができます。

しかし、箱庭を知らない人や、シミュレーション技術に詳しくない人、
自分でシステムを構築することが難しい人にとっては、

> 「箱庭は面白そうだけど、難しい」

で終わってしまうことがあります。

私たちは、むしろそうした人たちにこそ箱庭を使ってほしいと考えています。

`hakoniwa-business-pack` は、箱庭のコンポーネントを **Catalog** として整理し、
それらを組み合わせて「やりたいこと」を実現する方法を **Recipe** として蓄積するためのリポジトリです。

AIと対話しながら、

1. ユーザーの「やりたいこと」を整理する
2. 箱庭カタログから必要な部品を探す
3. 部品を組み合わせたプロダクトレシピを作る
4. 現在の箱庭部品で実現可能かを確認する
5. 可能であれば動作する最小デモを作る
6. デモから実際のプロダクトやサービスへつなげる

という流れを目指します。

---

## Concept

```text
箱庭リポジトリ群
       |
       v
  箱庭カタログ
       |
       v
User <-> AI
       |
       v
箱庭プロダクトレシピ
       |
       v
    デモ生成
       |
       v
 箱庭プロダクト
       |
       v
     User
```

従来の問いは、

> 箱庭で何ができますか？

でした。

このリポジトリでは、問いを逆にします。

> **あなたは箱庭で何をしたいですか？**

その問いに対して、AIと箱庭が伴走しながら、
必要な部品と実現までの道筋を一緒に考えます。

---

## Building Blocks

現在、箱庭ビジネスパックの候補として以下のコンポーネントを扱います。

### Simulation Core

- `hakoniwa-core-pro`
- `hakoniwa-conductor-pro`
- `hakoniwa-conductor-light`

### Physics / Environment

- `hakoniwa-mujoco-robots`
- `hakoniwa-envsim`

### Visualization

- `hakoniwa-godot`
- `hakoniwa-pdu-foxglove`

### Communication / Integration

- `hakoniwa-pdu-endpoint`
- `hakoniwa-pdu-bridge-core`
- `hakoniwa-pdu-rpc`

### Data Model

- `hakoniwa-pdu-registry`
- `hakoniwa-mbody-registry`

### Language Bindings

- `hakoniwa-pdu-python`
- `hakoniwa-pdu-javascript`

このリストは固定ではありません。
新しい箱庭コンポーネントが増えれば、Catalog に追加していきます。

---

## Catalog

Catalog は、各コンポーネントについて、たとえば次の情報を整理します。

- 何ができるか
- 何ができないか
- 入力と出力
- 必要な依存関係
- 接続できる他のコンポーネント
- 対応プラットフォーム
- 必要な計算リソース
- GPU の必要性
- 典型的なユースケース
- 既知の制約
- 利用可能なデモ

AIや人間がCatalogを参照することで、

> 「この要求なら、どの箱庭部品を使えばよいか」

を判断できる状態を目指します。

---

## Recipe

Recipe は、ユーザーの「やりたいこと」を実現するための、
箱庭コンポーネントの組み合わせ方です。

例えば、

> MuJoCoで動くロボットにカメラを追加して、その画像を外部ツールで可視化したい

という要求に対して、

```text
Robot Model
    |
    v
hakoniwa-mbody-registry
    |
    v
hakoniwa-mujoco-robots
    |
 Camera PDU
    |
    v
hakoniwa-pdu-endpoint
    |
    v
Visualization
```

のような構成をRecipeとして表現します。

Recipeには、単なるコンポーネント一覧だけでなく、次のような情報を記述します。

- ユーザーの目的
- 構成
- 採用したコンポーネント
- 各コンポーネントの役割
- 接続方法
- 実現可能性
- 不足している機能
- 必要な追加開発
- 最小デモの作り方

---

## Runtime Primer

Catalog は部品表、Recipe は構成書です。
ただし、箱庭のRecipeを作るには、その下にあるランタイムの定石も理解する必要があります。

[`docs/hakoniwa-runtime-primer.md`](docs/hakoniwa-runtime-primer.md) では、次のような前提を整理しています。

- 箱庭アセットとは何か
- 箱庭コアランタイム、共有メモリ、PDU空間がなぜ必要か
- PDUとは何か、`hakoniwa-pdu-registry` とどう関係するか
- プロセス起動とシミュレーション時刻開始の違い
- Conductor は構成全体で原則1つにすること
- Hakoniwa Launcher の `before_start` / `after_start` の考え方
- アセットではない外部クライアントがPDU/Bridge/RPCでゆるく参加する形
- 長時間プロセスの停止、cleanup、検証観点

AIがRecipeやDemo手順を書く前に読むべき、箱庭ランタイムの入門です。

---

## Demo

Recipe が実際に成立することを確認できる場合は、最小構成の Demo を作ります。

```text
Idea
  |
  v
Catalog Search
  |
  v
Recipe
  |
  v
Demo
  |
  v
Product
```

Demo は単なるサンプルではありません。

そのRecipeが、

> **「現在の箱庭コンポーネントを使って本当に実現できる」**

ことを示す Proof of Concept です。

確認されたRecipeとDemoを蓄積することで、
次のユーザーはより短い時間で同じ価値を利用できるようになります。

---

## Repository Structure

初期構成として、以下を想定しています。

```text
hakoniwa-business-pack/
├── catalog/
│   └── component definitions
│
├── docs/
│   └── Hakoniwa runtime primer
│
├── recipes/
│   └── product recipes
│
├── demos/
│   └── runnable minimal demonstrations
│
├── usecases/
│   └── user problems and ideas
│
└── README.md
```

今後、AIから扱いやすくするため、Catalog や Recipe は Markdown だけでなく、
YAML / JSON などの機械可読形式で管理することも検討します。

---

## Goal

このプロジェクトのゴールは、箱庭に詳しい人を増やすことだけではありません。

**箱庭に詳しくなくても、自分の「やりたいこと」からシミュレーションを作り始められること。**

そして、

```text
問い
 ↓
AIとの対話
 ↓
Catalog
 ↓
Recipe
 ↓
Demo
 ↓
Product
```

という流れを作ることです。

箱庭の技術資産を、ユーザーにとって意味のあるプロダクトやサービスへ変換する。

それが `hakoniwa-business-pack` の目的です。
