# 箱庭コンポーネント・アセットガイド

## 1. はじめに

本ドキュメントは、箱庭のベースエコシステムの上で利用できる主要なコンポーネントやアセットが、エコシステム全体のどこに位置するかを説明するためのガイドです。

箱庭の共通基盤である PDU、PDU Registry、MBody Registry、PDU Endpoint、PDU Bridge Core、PDU RPC、Core PRO、Conductor PRO、Conductor Light については、[箱庭ベースエコシステムガイド](hakoniwa-base-ecosystem-ja.md) を参照してください。

本ガイドの目的は、単なるリポジトリ一覧を提供することではありません。「何を実現したいか」から、どのコンポーネントが関係するのかを理解できる地図を提供することです。個々の Capability、Interface、制約については Catalog を参照してください。

## 2. 全体像

主要コンポーネントは、概念的に次の役割で捉えられます。

### モデルと世界を準備する

- `hakoniwa-pdu-registry` — アセット間で交換する情報・データモデル
- `hakoniwa-mbody-registry` — ロボットなどの身体モデルを MuJoCo / Godot などへ展開
- `hakoniwa-envsim` — 環境・世界モデルの作成、変換、可視化、問い合わせ

### シミュレーションを実行する

- `hakoniwa-mujoco-robots` — MuJoCo ベースのロボット物理シミュレーション
- `hakoniwa-godot` — 3D 可視化、インタラクション、PDU 交換を行うシミュレーション参加環境
- `hakoniwa-drone-core` — ドローン分野に特化した統合シミュレーションアセット

### アプリケーションを開発・接続する

- `hakoniwa-pdu-python` — Python 向け PDU SDK・通信・変換・実行支援機能
- `hakoniwa-pdu-javascript` — Browser / Node.js から PDU を扱うためのライブラリ
- `hakoniwa-pdu-ros` — PDU と ROS 2 message を実行時に型変換して転送する軽量ブリッジ

### 観測・可視化・インタラクションする

- `hakoniwa-pdu-foxglove` — PDU を Foxglove へ出力するアダプタ
- `hakoniwa-threejs-drone` — Web ブラウザでドローンを 3D 可視化
- `hakoniwa-scratch` — Scratch から箱庭を操作・連携

これらは排他的な分類ではありません。Godot のように可視化とシミュレーション参加の両方を担うものや、Envsim のように環境モデリングと外部データ変換の両方を担うものがあります。

## 3. モデルと世界を準備する

### 3.1 PDU Registry — 情報モデルを共有可能にする

PDU Registry は、共通の PDU 定義から、各プログラミング言語や実行環境で利用するデータ型、Converter、サイズ情報、オフセット情報などを生成します。

箱庭エコシステムにおける「情報モデルの共通化と展開」を担当します。

詳細は [箱庭ベースエコシステムガイド](hakoniwa-base-ecosystem-ja.md) および Catalog の `hakoniwa-pdu-registry` を参照してください。

### 3.2 MBody Registry — 身体モデルを複数の実行環境へ展開する

MBody Registry は、ロボットなどの身体モデルを共通ソースから複数の実行環境向けアセットへ変換・展開する基盤です。

代表的には、MuJoCo で利用する MJCF、Godot などの 3D 環境で利用する GLB や関連アセット、URDF、PDU 関連アセットなどを生成・管理します。

これにより、MuJoCo 用と Godot 用のロボットを完全に別々に管理するのではなく、同じ身体モデルを起点として、物理シミュレーション側と 3D 可視化・インタラクション側へ展開できます。

PDU Registry が「情報モデル」を展開するのに対し、MBody Registry は「身体モデル」を展開する変換ハブです。

詳細は Catalog の `hakoniwa-mbody-registry` を参照してください。

### 3.3 Envsim — シミュレーションする世界を構築する

Hakoniwa Envsim は、ロボットやドローンが存在する環境・世界をモデル化するためのツールキットです。

風、温度、気圧、GPS 強度、空間的な境界などの環境情報を作成、可視化、編集し、シミュレーション中のアセットから問い合わせ可能な環境モデルとして利用できます。

また、Envsim の役割は環境場のモデル化だけではありません。PLATEAU などの外部データを取り込み、MuJoCo などのシミュレーション環境で利用できる形へ変換するなど、現実世界のデータからシミュレーション世界を構築する用途も担います。

この意味で Envsim は、Environment Modeling、World Generation、Data Conversion を横断する環境・世界モデル基盤です。

MBody Registry が「世界の中で動く主体」を供給するのに対し、Envsim は「その主体が存在し、活動する世界」を供給します。

詳細は Catalog の `hakoniwa-envsim` を参照してください。

## 4. シミュレーションを実行する

### 4.1 Hakoniwa MuJoCo Robots — ロボットの物理シミュレーション

`hakoniwa-mujoco-robots` は、MuJoCo を利用してロボットの物理シミュレーションを行うための箱庭アセット群です。

MuJoCo 自体を単純にラップするものではなく、ロボットモデル、センサ、アクチュエータ、Python コントローラ、PDU 連携、TurtleBot3 などのサンプルを含みます。

MBody Registry から生成された身体モデルを MuJoCo の物理シミュレーションへ展開し、PDU を介して制御プログラムや可視化環境と状態を交換できます。

詳細は Catalog の `hakoniwa-mujoco-robots` を参照してください。

### 4.2 Hakoniwa Godot — 3D 世界と箱庭を接続する

`hakoniwa-godot` は、Godot を箱庭エコシステムの参加アセットとして利用するための統合コンポーネントです。

3D 可視化だけでなく、PDU の送受信、ロボット状態の同期、制御、インタラクション、必要に応じた物理時間との同期などを行えます。

MBody Registry から Godot 向け身体モデルやアセットを生成し、MuJoCo などと PDU で状態を共有することで、「物理計算は MuJoCo」「3D 世界とユーザーインタラクションは Godot」という役割分担も可能です。

詳細は Catalog の `hakoniwa-godot` を参照してください。

### 4.3 Hakoniwa Drone — ドローン分野の統合シミュレーションアセット

`hakoniwa-drone-core` は、ドローンの機体ダイナミクス、PX4 / ArduPilot 連携、PDU 連携、Python 制御 API、複数ドローン、センサ・環境モデルなどを組み合わせたドメイン特化型の箱庭アセットです。

箱庭のベースエコシステムを利用して構築された代表的なドメインアセットであり、Envsim などの環境モデルや外部制御システムと組み合わせられます。

同一コードベースを、非商用向け Hakoniwa Drone Core と商用向け Hakoniwa Drone PRO のデュアルライセンスで提供します。

詳細は Catalog の `hakoniwa-drone-core` を参照してください。

## 5. アプリケーションを開発・接続する

### 5.1 Hakoniwa PDU Python — Python 向け SDK・ユーティリティ群

`hakoniwa-pdu-python` は Python から箱庭 PDU を扱うためのライブラリですが、単純な言語バインディングに限定されません。

PDU の読み書き、Python オブジェクトと PDU データの変換、Binary / JSON 変換、WebSocket Topic、WebSocket RPC、SHM backend など、Python から箱庭へアクセスするための複数の機能を包含しています。

歴史的に機能が集約されているため、「Python 対応」という一語で扱うのではなく、目的に応じて必要な Capability を確認して利用することが重要です。

詳細は Catalog の `hakoniwa-pdu-python` を参照してください。

### 5.2 Hakoniwa PDU JavaScript — Web / Node.js から PDU を扱う

`hakoniwa-pdu-javascript` は、Browser や Node.js から箱庭 PDU を読み書き・変換するためのライブラリです。

WebSocket を利用した PDU 通信を通じて、Web アプリケーションやブラウザベースの可視化システムを箱庭へ参加させられます。

詳細は Catalog の `hakoniwa-pdu-javascript` を参照してください。

### 5.3 Hakoniwa PDU ROS — PDU と ROS 2 message を手軽に橋渡しする

`hakoniwa-pdu-ros` は、Python ランタイム上で PDU 型と ROS 2 message 型を動的に解釈し、対応するフィールドを型変換しながら双方向に転送する軽量ブリッジです。

既存の ROS 2 Node と箱庭アセットを手軽に接続したい場合に適しています。

これは、PDU Endpoint が通信端点として提供する ROS 2 / Zenoh 系の接続とは役割が異なります。PDU Endpoint は通信基盤として接続方式を抽象化するのに対し、PDU ROS は PDU データモデルと ROS 2 message モデルを Python ランタイム上で変換・転送することに重点を置きます。

そのため、Zenoh を前提とせず、既存 ROS 2 システムと PDU を軽量に橋渡ししたい場合の選択肢になります。

詳細は Catalog の `hakoniwa-pdu-ros` を参照してください。

## 6. 観測・可視化・インタラクション

### 6.1 Hakoniwa PDU Foxglove — PDU を Foxglove で観測する

`hakoniwa-pdu-foxglove` は、PDU Endpoint と組み合わせ、Foxglove 互換の CDR payload を WebSocket 経由で公開するためのアダプタです。

箱庭独自 UI を新たに開発するのではなく、既存の Foxglove エコシステムを利用してシミュレーションデータを観測できます。

詳細は Catalog の `hakoniwa-pdu-foxglove` を参照してください。

### 6.2 Hakoniwa Three.js Drone — Web ブラウザでドローンを可視化する

`hakoniwa-threejs-drone` は、Three.js を利用してドローンシミュレーションを Web ブラウザ上で可視化するコンポーネントです。

Godot が高度な 3D 世界やインタラクションを構築できる参加環境であるのに対し、Three.js Drone はブラウザを利用した軽量な 3D 可視化手段として位置付けられます。

詳細は Catalog の `hakoniwa-threejs-drone` を参照してください。

### 6.3 Hakoniwa Scratch — ビジュアルプログラミングから箱庭を操作する

`hakoniwa-scratch` は、Scratch のビジュアルプログラミング環境から箱庭を操作・連携するためのコンポーネントです。

教育、プロトタイピング、デモ、非プログラマによる操作など、箱庭への入口をより高い抽象度のユーザーインタフェースへ広げます。

詳細は Catalog の `hakoniwa-scratch` を参照してください。

## 7. コンポーネントを組み合わせる

箱庭の特徴は、これらを単独で利用することではなく、目的に応じて組み合わせられることにあります。

### ロボット物理シミュレーションと 3D 可視化

```text
MBody Registry
  |-- MJCF --> MuJoCo Robots -- PDU --> Godot
  `-- 3D assets ---------------------> Godot
```

同じ身体モデルを起点に、MuJoCo で物理計算し、Godot で 3D 世界を構築できます。

### 都市・環境データとドローンシミュレーション

```text
PLATEAU / environment data
        |
        v
      Envsim
        |
        v
Simulation World <--> Hakoniwa Drone
        |
        v
Godot / Three.js / Foxglove
```

Envsim で外部データや環境場をシミュレーション世界へ変換し、ドローンなどのアセットと組み合わせます。

### ROS 2 と手軽に接続する

```text
ROS 2 Node
    ^
    | runtime type conversion
    v
Hakoniwa PDU ROS
    ^
    | PDU
    v
Hakoniwa Asset
```

既存 ROS 2 システムとの軽量な接続には PDU ROS を利用できます。通信基盤自体を含めて分散構成を設計する場合は、PDU Endpoint や PDU Bridge Core を検討します。

## 8. Catalog、Recipe、Demo との関係

本ドキュメントは Catalog に登録された主要コンポーネントの「位置付け」を理解するためのガイドです。

```text
Base Ecosystem Guide
  -> Component / Asset Guide
  -> Catalog
  -> Recipe
  -> Demo
```

- **Base Ecosystem Guide**: 箱庭を構成する共通基盤と設計思想を理解する
- **Component / Asset Guide**: 利用可能な主要コンポーネントがエコシステムのどこに位置するかを理解する
- **Catalog**: 各コンポーネントの Capability、Interface、制約を確認する
- **Recipe**: 目的に応じて複数のコンポーネントを組み合わせる
- **Demo**: Recipe を実際に動作するシステムとして検証する

人間または AI は、まず箱庭の基本構造とコンポーネントの位置付けを理解した上で、Catalog から具体的な部品を選択し、Recipe として組み合わせます。

## 9. 対象範囲について

本ガイドは、現在の主要な箱庭エコシステムを理解するためのコンポーネントを対象としています。

Catalog には、このほかにも組込み・ECU シミュレーションなどのコンポーネントが登録される場合があります。それらは Catalog から参照できますが、本ガイドでは現時点の主要マップを明確にするため対象外としています。
