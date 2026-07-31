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

  def test_requirements_reject_false_capability_and_invalid_limit
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

    assert errors.any? { |error| error.include?("shared_memory must be true") }
    assert errors.any? { |error| error.include?("min must be a positive integer") }
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
