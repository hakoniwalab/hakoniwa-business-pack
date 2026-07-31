# Foundation Component リリースチェックリスト

[English](foundation-component-release-checklist.md)

この文書は、Foundationで再利用される箱庭コンポーネントに新しい外部機能を追加し、
新しいバージョンとして公開するときのメンテナー契約を定義します。

目的は、ソースコードだけが新しくなり、package metadata、Component Receipt、
Catalog、Recipe、ローカルFoundationのいずれかが古いまま残る状態を防ぐことです。

## 1. 外部契約を分類する

新機能を次の二つに分けて記録します。

- `capability`: install済みコンポーネントが実際にその機能を提供するか。
- `version.min`: その機能を正式な外部契約として利用できる最小リリース。

正式リリース以降でなければ利用できない機能は、Recipeで両方を要求します。

```yaml
foundation_requirements:
  hakoniwa-pdu-python:
    version:
      min: 1.6.5
    capabilities:
      launcher_background_lifecycle: true
```

バージョンだけで機能を推測せず、Capabilityだけで正式リリース境界を失わないことが
重要です。

## 2. owner repositoryを更新する

次を同じリリース作業として扱います。

- packageまたはproject metadataのversion。
- user-facing README／設計文書／CLI help。
- `hako.py`が生成するComponent ReceiptのCapability。
- install済み成果物を検証する`smoke`。
- manifest defaultと既存native契約の互換性テスト。
- 対応platformのCI。

ReceiptへCapabilityを書く前に、`smoke`でinstall済み成果物からその機能を確認します。
ソースツリーをimportできるだけでは不十分です。

## 3. build出力を一意にする

同じbuild directoryへ複数バージョンのwheelやpackageを残さないでください。

推奨規約:

- build開始前に、コンポーネント自身が所有する古い成果物だけを削除する。
- workspace全体や共有directoryを広く削除しない。
- install候補が複数ある場合は、暗黙に一つを選ばず失敗させる。
- Receiptは、実際にinstallして`smoke`した成果物から生成する。

Python wheelの例:

```text
build/
  hakoniwa_pdu-1.6.3-py3-none-any.whl
  hakoniwa_pdu-1.6.5-py3-none-any.whl
```

この状態を許すと、ソースと違う世代を誤ってinstallする可能性があります。
削除対象は`hakoniwa_pdu-*.whl`のようにowner packageへ限定します。

## 4. Python環境の同一性を確認する

`pip install`の成功だけでFoundation更新済みとは判断しません。
Blender、system Python、pyenv、Homebrew、別venvへinstallされている可能性があります。

FoundationのPython実体で次を確認します。

```bash
work/foundation/install/python/bin/python -c \
  "import sys; print(sys.executable); print(sys.prefix)"

work/foundation/install/python/bin/python -m pip show <package>
```

最低限、次の証拠を揃えます。

- package version。
- `pip show`の`Location`。
- `sys.executable`と`sys.prefix`。
- 必要なmodule／CLIのsmoke import。
- Component Receiptのversion、source revision、Capability。

他のPython環境へのinstall成功を、Foundation venvの更新証拠として使用しません。

## 5. Business Packを更新する

owner repositoryのリリース後、Business Packでは次を更新します。

- Catalogの`verification.source_revision`と`verified_at`。
- Capabilityの説明とevidence revision。
- 正式な最小リリースがあるRecipeの`version.min`。
- Recipeが実際に必要とするCapability。
- Foundation schema／validator／test。
- 実行または再利用判定のvalidation evidence。

Catalogのrevisionは、機能実装だけでなく、公開versionを確定したrevisionも参照します。

## 6. Foundationで前後を検証する

更新前のReceiptが拒否されることと、更新後のReceiptが受理されることを両方確認します。

```bash
python3.12 tools/foundation.py doctor --recipe <recipe.yaml>
python3.12 tools/foundation.py plan --recipe <recipe.yaml>
python3.12 tools/foundation.py build --recipe <recipe.yaml>
python3.12 tools/foundation.py doctor --recipe <recipe.yaml>
```

期待する遷移:

```text
old version / missing capability
  -> INCOMPATIBLE
  -> rebuild only affected component and known downstream dependencies
  -> smoke installed artifact
  -> SATISFIED
```

単にbuildが成功したことではなく、最終Receiptとinstall済み成果物がRecipe要求を
満たすことを確認します。

## 7. リリース完了条件

- [ ] version metadataが更新されている。
- [ ] user-facing外部契約が文書化されている。
- [ ] Receiptが新Capabilityを記録する。
- [ ] smokeがinstall済み成果物でCapabilityを確認する。
- [ ] stale build artifactを選択できない。
- [ ] owner repositoryのunit test／CIが通る。
- [ ] Catalogのrevisionとevidenceが更新されている。
- [ ] 必要なRecipeに`version.min`とCapabilityが宣言されている。
- [ ] 古いFoundation Receiptが`INCOMPATIBLE`になる。
- [ ] 再構築後のFoundationが`SATISFIED`になる。
- [ ] Demoのprocess lifecycleと実際の振る舞いを区別して検証している。

このチェックリストは、リリース番号を付けるためだけの手順ではありません。
将来の人やAIが、なぜFoundationを再利用できるのかを説明できる状態を作るための
手順です。
