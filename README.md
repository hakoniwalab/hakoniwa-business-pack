# hakoniwa-business-pack

> **A catalog and recipe hub for composing Hakoniwa components with AI to turn user ideas into working demos and products.**

## AI Bootstrap Prompt

If you are an AI agent, do not summarize this repository after reading only this
README.

Before answering that you understand `hakoniwa-business-pack`, read these files
in order:

1. `AGENTS.md`
2. `docs/hakoniwa-base-ecosystem-ja.md`
3. `docs/hakoniwa-component-asset-guide-ja.md`
4. `catalog/index.yaml`
5. `catalog/schema.yaml`
6. `docs/hakoniwa-runtime-primer.md`
7. relevant `catalog/components/*.yaml`
8. `recipes/README.md`
9. relevant `recipes/examples/*.yaml`

The documents have different responsibilities:

- **Base Ecosystem Guide** explains the shared Hakoniwa foundations and how PDU,
  Registry, Endpoint, Bridge, RPC, Core, and Conductor relate.
- **Component / Asset Guide** explains where major simulation, SDK, integration,
  visualization, and interaction components fit in the ecosystem.
- **Catalog** provides component-level facts, capabilities, interfaces,
  dependencies, and constraints.
- **Runtime Primer** explains the runtime rules needed to make a concrete
  composition executable.
- **Recipes** describe system compositions that combine those components.

Then answer in this shape:

```text
I have read:
- AGENTS.md: yes/no
- docs/hakoniwa-base-ecosystem-ja.md: yes/no
- docs/hakoniwa-component-asset-guide-ja.md: yes/no
- catalog/index.yaml: yes/no
- catalog/schema.yaml: yes/no
- docs/hakoniwa-runtime-primer.md: yes/no
- relevant component catalogs: <names or none>
- relevant recipes: <names or none>

Current understanding:
- what this repository is
- how the Hakoniwa ecosystem is structured
- where the relevant components fit
- what Hakoniwa runtime assumptions matter
- what can be proposed from catalog evidence
- what is feasible, verified, unknown, or blocked
```

Do not jump from "Can Hakoniwa do X?" to adjacent source repositories or local
execution. First understand the ecosystem and component roles, then use the
Catalog and Recipes. If no Recipe exists, propose a Recipe-shaped plan with
`feasibility` and `validation` states. Run build, fetch, install, launch, GUI,
browser, or server commands only after the user asks for local execution.

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
2. 箱庭エコシステムと主要コンポーネントの位置付けを理解する
3. 箱庭カタログから必要な部品を探す
4. 部品を組み合わせたプロダクトレシピを作る
5. 現在の箱庭部品で実現可能かを確認する
6. 可能であれば動作する最小デモを作る
7. デモから実際のプロダクトやサービスへつなげる

という流れを目指します。

---

## Concept

```text
User Goal
   |
   v
Ecosystem Guide
   |
   v
Component / Asset Guide
   |
   v
Hakoniwa Catalog
   |
   v
User <-> AI
   |
   v
Hakoniwa Product Recipe
   |
   v
Demo
   |
   v
Product / Service
```

従来の問いは、

> 箱庭で何ができますか？

でした。

このリポジトリでは、問いを逆にします。

> **あなたは箱庭で何をしたいですか？**

その問いに対して、AIと箱庭が伴走しながら、
必要な部品と実現までの道筋を一緒に考えます。

---

## Guides

### Base Ecosystem Guide

[`docs/hakoniwa-base-ecosystem-ja.md`](docs/hakoniwa-base-ecosystem-ja.md) は、
箱庭を構成する共通基盤とその関係性を説明します。

主な対象は次のとおりです。

- PDU / PDU Registry
- MBody Registry
- PDU Endpoint
- PDU Bridge Core
- PDU RPC
- Core PRO
- Conductor PRO / Conductor Light
- JSON を中心とした宣言型のシステム構成

### Component / Asset Guide

[`docs/hakoniwa-component-asset-guide-ja.md`](docs/hakoniwa-component-asset-guide-ja.md) は、
Catalog にある主要コンポーネントがエコシステムのどこに位置するかを説明します。

たとえば、次のような関係を扱います。

- PDU Registry: 情報モデル
- MBody Registry: 身体モデルと MuJoCo / Godot 向け変換
- Envsim: 環境・世界モデル、PLATEAU 等からの変換
- MuJoCo / Godot / Hakoniwa Drone: シミュレーション実行
- PDU Python / JavaScript: アプリケーション SDK
- PDU ROS: PDU と ROS 2 message の軽量な実行時変換ブリッジ
- Foxglove / Three.js / Scratch: 観測、可視化、インタラクション

---

## Building Blocks

現在、箱庭ビジネスパックでは以下のようなコンポーネントを扱います。
正確で最新の一覧は `catalog/index.yaml` を参照してください。

### Simulation Core

- `hakoniwa-core-pro`
- `hakoniwa-conductor-pro`
- `hakoniwa-conductor-light`

### Physics / Environment / Domain

- `hakoniwa-mujoco-robots`
- `hakoniwa-envsim`
- `hakoniwa-drone-core`

### Visualization / Interaction

- `hakoniwa-godot`
- `hakoniwa-pdu-foxglove`
- `hakoniwa-threejs-drone`
- `hakoniwa-scratch`

### Communication / Integration

- `hakoniwa-pdu-endpoint`
- `hakoniwa-pdu-bridge-core`
- `hakoniwa-pdu-rpc`
- `hakoniwa-pdu-ros`

### Data / Body Model

- `hakoniwa-pdu-registry`
- `hakoniwa-mbody-registry`

### Language SDKs

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
ただし、箱庭のRecipeを実際に動かすには、その下にあるランタイムの定石も理解する必要があります。

[`docs/hakoniwa-runtime-primer.md`](docs/hakoniwa-runtime-primer.md) では、次のような実行時の前提を整理しています。

- 箱庭コアランタイムと共有メモリを利用する構成
- `pdutypes` と `pdudef` の役割
- プロセス起動とシミュレーション時刻開始の違い
- Conductor のランタイム所有関係
- Hakoniwa Launcher の `before_start` / `after_start`
- 外部クライアントが PDU / Bridge / RPC で参加する形
- 長時間プロセスの停止、cleanup、検証観点

PDU や Registry、Endpoint、Bridge、RPC、Core / Conductor の概念的な位置付けは、
Runtime Primer ではなく Base Ecosystem Guide を参照してください。

### Demo Recording Runbook

[`docs/demo-recording-runbook-ja.md`](docs/demo-recording-runbook-ja.md) は、
検証済みデモを動画素材として録画するための実行手順です。

現在は次のデモを対象にしています。

- FR5 アーム
- AgileX Tracer
- Unitree Go1
- Hakoniwa Drone

録画前の `git status` 確認、Python 環境、Conductor / asset / `hako-cmd start`
の順序、各デモの成功サイン、停止方法をまとめています。

---

## Demo

Recipe が実際に成立することを確認できる場合は、最小構成の Demo を作ります。

```text
Idea
  |
  v
Ecosystem / Component Map
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

```text
hakoniwa-business-pack/
├── catalog/
│   └── component definitions
│
├── docs/
│   ├── hakoniwa-base-ecosystem-ja.md
│   ├── hakoniwa-component-asset-guide-ja.md
│   ├── hakoniwa-runtime-primer.md
│   └── demo-recording-runbook-ja.md
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
├── AGENTS.md
└── README.md
```

Catalog や Recipe は、AIから扱いやすい YAML / JSON などの機械可読形式を中心に管理します。

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
Ecosystem / Component Map
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
