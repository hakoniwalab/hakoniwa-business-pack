# frozen_string_literal: true

require "minitest/autorun"
require "yaml"
require "date"
require_relative "native_runtime_validation"

def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  YAML.load_file(path)
end

class NativeRuntimeValidationTest < Minitest::Test
  ROOT = File.expand_path("..", __dir__)
  SCHEMA = load_yaml_file(File.join(__dir__, "native-runtime.yaml"))
  CATALOG_ENTRY = load_yaml_file(
    File.join(ROOT, "catalog", "components", "hakoniwa-drone-core.yaml")
  )
  RECIPE = load_yaml_file(
    File.join(ROOT, "recipes", "examples", "drone-fleet-single-host.yaml")
  )
  PERFORMANCE_RECIPE = load_yaml_file(
    File.join(
      ROOT,
      "recipes",
      "examples",
      "drone-fleet-multi-process-scaling.yaml"
    )
  )

  def test_current_catalog_and_recipe_contracts_are_valid
    assert_empty NativeRuntimeValidation.validate_catalog(
      CATALOG_ENTRY["native_runtime"], SCHEMA, label: "catalog"
    )
    assert_empty NativeRuntimeValidation.validate_recipe(
      RECIPE["native_runtime_requirements"],
      {CATALOG_ENTRY["id"] => CATALOG_ENTRY},
      SCHEMA,
      label: "recipe"
    )
    assert_empty NativeRuntimeValidation.validate_recipe(
      PERFORMANCE_RECIPE["native_runtime_requirements"],
      {CATALOG_ENTRY["id"] => CATALOG_ENTRY},
      SCHEMA,
      label: "performance recipe"
    )
  end

  def test_catalog_rejects_unknown_dependency_inspector
    value = Marshal.load(Marshal.dump(CATALOG_ENTRY["native_runtime"]))
    value.dig("profiles", "public-v4.0.0", "platforms", "macos")[
      "dependency_inspector"
    ] = "shell-command"

    errors = NativeRuntimeValidation.validate_catalog(
      value, SCHEMA, label: "catalog"
    )

    assert errors.any? { |error| error.include?("dependency_inspector is invalid") }
  end

  def test_catalog_rejects_schema_required_field_omission
    value = Marshal.load(Marshal.dump(CATALOG_ENTRY["native_runtime"]))
    value.dig("profiles", "public-v4.0.0", "platforms", "linux").delete(
      "required_libraries"
    )

    errors = NativeRuntimeValidation.validate_catalog(
      value, SCHEMA, label: "catalog"
    )

    assert errors.any? { |error| error.include?("missing required_libraries") }
  end

  def test_recipe_rejects_role_not_declared_by_catalog_profile
    value = Marshal.load(
      Marshal.dump(RECIPE["native_runtime_requirements"])
    )
    value.dig("components", "hakoniwa-drone-core", "required_roles") << "unknown"

    errors = NativeRuntimeValidation.validate_recipe(
      value,
      {CATALOG_ENTRY["id"] => CATALOG_ENTRY},
      SCHEMA,
      label: "recipe"
    )

    assert errors.any? { |error| error.include?("role is not available") }
  end
end
