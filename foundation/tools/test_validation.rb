#!/usr/bin/env ruby
# frozen_string_literal: true

require "minitest/autorun"
require "set"
require_relative "validation"

class FoundationValidationTest < Minitest::Test
  CATALOG_IDS = Set.new(%w[
    hakoniwa-core-pro
    hakoniwa-pdu-bridge-core
  ]).freeze

  def test_valid_requirements
    requirements = {
      "hakoniwa-core-pro" => {
        "version" => {
          "min" => "1.6.5"
        },
        "capabilities" => {
          "shared_memory" => true,
          "hako_cmd" => true
        },
        "build_limits" => {
          "asset_num" => {"min" => 16}
        }
      }
    }

    assert_empty FoundationValidation.validate_requirements(
      requirements,
      label: "recipe.yaml",
      catalog_ids: CATALOG_IDS
    )
  end

  def test_requirements_reject_invalid_minimum_version
    requirements = {
      "hakoniwa-core-pro" => {
        "version" => {"min" => "latest"}
      }
    }

    errors = FoundationValidation.validate_requirements(
      requirements,
      label: "recipe.yaml",
      catalog_ids: CATALOG_IDS
    )

    assert errors.any? { |error| error.include?("dotted numeric version") }
  end

  def test_requirements_reject_unknown_component_and_field
    requirements = {
      "unknown-component" => {
        "capabilities" => {"shared_memory" => true},
        "install_dir" => "/tmp/install"
      }
    }

    errors = FoundationValidation.validate_requirements(
      requirements,
      label: "recipe.yaml",
      catalog_ids: CATALOG_IDS
    )

    assert errors.any? { |error| error.include?("unknown component") }
    assert errors.any? { |error| error.include?("unknown fields: install_dir") }
  end

  def test_requirements_accept_false_capability_and_reject_invalid_limit
    requirements = {
      "hakoniwa-core-pro" => {
        "capabilities" => {"shared_memory" => false},
        "build_limits" => {"asset_num" => {"min" => 0}}
      }
    }

    errors = FoundationValidation.validate_requirements(
      requirements,
      label: "recipe.yaml",
      catalog_ids: CATALOG_IDS
    )

    refute errors.any? { |error| error.include?("shared_memory") }
    assert errors.any? { |error| error.include?("min must be a positive integer") }
  end

  def test_managed_workspace_requires_foundation_requirements
    errors = FoundationValidation.validate_workspace_contract(
      {"workspace" => {"mode" => "managed"}}, nil, label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("managed Workspace Recipe") }
    assert errors.any? { |error| error.include?("do not bypass") }
  end

  def test_managed_workspace_accepts_non_empty_foundation_requirements
    errors = FoundationValidation.validate_workspace_contract(
      {"workspace" => {"mode" => "managed"}},
      {"hakoniwa-core-pro" => {"capabilities" => {"shared_memory" => true}}},
      label: "recipe.yaml"
    )
    assert_empty errors
  end

  def test_legacy_recipe_can_omit_workspace_and_foundation_requirements
    assert_empty FoundationValidation.validate_workspace_contract(
      {"required_for" => "executable-demo"}, nil, label: "recipe.yaml"
    )
  end

  def test_required_foundation_contract_accepts_managed_recipe
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "required", "reason" => "Uses Core shared memory and hakopy."},
      {"hakoniwa-core-pro" => {"capabilities" => {"python_binding" => true}}},
      {
        "workspace" => {"mode" => "managed"},
        "python" => {
          "environment" => "foundation-venv",
          "path" => "work/foundation/install/python",
          "version" => "3.12",
          "hakopy_available" => true
        },
        "hakoniwa" => {"install_prefix" => "work/foundation/install"}
      },
      label: "recipe.yaml"
    )
    assert_empty errors
  end

  def test_required_foundation_contract_rejects_missing_requirements
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "required", "reason" => "Uses hakopy."}, nil,
      {"workspace" => {"mode" => "managed"}}, label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("non-empty foundation_requirements") }
  end

  def test_required_foundation_contract_rejects_system_prefix
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "required", "reason" => "Uses Core."},
      {"hakoniwa-core-pro" => {"capabilities" => {"shared_memory" => true}}},
      {
        "workspace" => {"mode" => "managed"},
        "hakoniwa" => {"install_prefix" => "/usr/local/hakoniwa"}
      },
      label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("work/foundation/install") }
  end

  def test_hakopy_runtime_rejects_non_foundation_python_and_missing_core_binding
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "required", "reason" => "Uses hakopy."},
      {"hakoniwa-core-pro" => {"capabilities" => {"shared_memory" => true}}},
      {
        "workspace" => {"mode" => "managed"},
        "python" => {
          "environment" => "venv",
          "path" => ".venv",
          "version" => "3.11",
          "hakopy_available" => true
        },
        "hakoniwa" => {"install_prefix" => "work/foundation/install"}
      },
      label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("python.environment foundation-venv") }
    assert errors.any? { |error| error.include?("python.path work/foundation/install/python") }
    assert errors.any? { |error| error.include?("Python 3.12") }
    assert errors.any? { |error| error.include?("capability python_binding") }
  end

  def test_foundation_requirements_require_explicit_contract_classification
    errors = FoundationValidation.validate_foundation_contract(
      nil,
      {"hakoniwa-core-pro" => {"capabilities" => {"shared_memory" => true}}},
      {"workspace" => {"mode" => "managed"}},
      label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("classified by foundation_contract") }
  end

  def test_foundation_runtime_signals_require_explicit_contract_classification
    errors = FoundationValidation.validate_foundation_contract(
      nil, nil,
      {"python" => {"hakopy_available" => "required-for-runtime"}},
      label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("Foundation runtime signals require explicit foundation_contract") }
    assert errors.any? { |error| error.include?("python.hakopy_available") }
  end

  def test_not_required_foundation_contract_requires_reason_and_rejects_managed_workspace
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "not_required", "reason" => ""}, nil,
      {"workspace" => {"mode" => "managed"}}, label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("reason must be a non-empty string") }
    assert errors.any? { |error| error.include?("must not use a managed Workspace") }
  end

  def test_not_required_foundation_contract_rejects_hakopy_runtime_signal
    errors = FoundationValidation.validate_foundation_contract(
      {"mode" => "not_required", "reason" => "Setup only."}, nil,
      {"python" => {"hakopy_available" => "required-for-runtime"}},
      label: "recipe.yaml"
    )
    assert errors.any? { |error| error.include?("python.hakopy_available") }
  end

  def test_foundation_usage_signals_detect_runtime_dependencies
    signals = FoundationValidation.foundation_usage_signals(
      {
        "shared_memory" => {"required" => true},
        "python" => {"hakopy_available" => true},
        "hakoniwa" => {"hako_cmd_access" => "required-for-demo"}
      }
    )
    assert_equal %w[shared_memory.required python.hakopy_available hakoniwa.hako_cmd_access], signals
  end

  def test_valid_receipt
    assert_empty FoundationValidation.validate_receipt(valid_receipt, label: "receipt.yaml")
  end

  def test_receipt_rejects_unknown_schema_version
    receipt = valid_receipt.merge("schema_version" => 2)

    errors = FoundationValidation.validate_receipt(receipt, label: "receipt.yaml")

    assert_includes errors, "receipt.yaml: schema_version must be 1"
  end

  def test_receipt_rejects_missing_field
    receipt = valid_receipt.dup
    receipt.delete("artifacts")

    errors = FoundationValidation.validate_receipt(receipt, label: "receipt.yaml")

    assert errors.any? { |error| error.include?("missing required fields: artifacts") }
  end

  def test_receipt_rejects_absolute_artifact_path
    receipt = valid_receipt
    receipt["artifacts"] = [{"path" => "/usr/local/lib/libhakoniwa.a", "kind" => "library"}]

    errors = FoundationValidation.validate_receipt(receipt, label: "receipt.yaml")

    assert errors.any? { |error| error.include?("relative install-prefix path") }
  end

  def test_receipt_accepts_soabi_python_binding_metadata
    receipt = valid_receipt.merge(
      "python" => {
        "binding_mode" => "soabi",
        "implementation" => "CPython",
        "executable" => "/usr/bin/python3.12",
        "version" => "3.12.10",
        "major" => 3,
        "minor" => 12,
        "soabi" => "cpython-312-test",
        "extension_suffix" => ".cpython-312-test.so",
        "artifact" => "share/hakoniwa/python/hakopy.cpython-312-test.so"
      }
    )

    assert_empty FoundationValidation.validate_receipt(receipt, label: "receipt.yaml")
  end

  def test_receipt_rejects_legacy_python_binding_metadata
    receipt = valid_receipt.merge(
      "python" => {
        "binding_mode" => "legacy",
        "implementation" => "CPython",
        "executable" => "python",
        "version" => "3.12.10",
        "major" => 3,
        "minor" => 12,
        "soabi" => "",
        "extension_suffix" => ".so",
        "artifact" => "/tmp/hakopy.so"
      }
    )

    errors = FoundationValidation.validate_receipt(receipt, label: "receipt.yaml")
    assert errors.any? { |error| error.include?("binding_mode must be soabi") }
    assert errors.any? { |error| error.include?("soabi must be a non-empty string") }
    assert errors.any? { |error| error.include?("relative install-prefix path") }
  end

  private

  def valid_receipt
    {
      "schema_version" => 1,
      "component" => {
        "id" => "hakoniwa-core-pro",
        "version" => "1.0.0",
        "source_revision" => "abc1234"
      },
      "platform" => {
        "os" => "macos",
        "architecture" => "arm64",
        "toolchain" => "apple-clang"
      },
      "install" => {"prefix" => "work/foundation/install"},
      "capabilities" => {"shared_memory" => true, "hako_cmd" => true},
      "build_limits" => {"asset_num" => 16},
      "dependencies" => {},
      "artifacts" => [
        {"path" => "bin/hako-cmd", "kind" => "executable"}
      ],
      "resolved_manifest" => "share/hakoniwa/receipts/resolved/hakoniwa-core-pro.yaml"
    }
  end
end
