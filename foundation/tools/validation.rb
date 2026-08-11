# frozen_string_literal: true

require "pathname"
require "set"

module FoundationValidation
  IDENTIFIER = /\A[a-z][a-z0-9_]*\z/.freeze
  DOTTED_NUMERIC_VERSION = /\A\d+(?:\.\d+)*\z/.freeze
  REQUIREMENT_FIELDS = Set.new(%w[version capabilities build_limits]).freeze
  RECEIPT_FIELDS = Set.new(%w[
    schema_version
    component
    platform
    install
    capabilities
    build_limits
    dependencies
    artifacts
    resolved_manifest
  ]).freeze

  module_function

  def foundation_usage_signals(execution_environment)
    return [] unless execution_environment.is_a?(Hash)

    signals = []
    shared_memory = execution_environment.dig("shared_memory", "required")
    signals << "shared_memory.required" if shared_memory == true || shared_memory.to_s.start_with?("required")
    hakopy = execution_environment.dig("python", "hakopy_available")
    signals << "python.hakopy_available" if hakopy == true || hakopy.to_s.start_with?("required")
    hako_cmd = execution_environment.dig("hakoniwa", "hako_cmd_access")
    signals << "hakoniwa.hako_cmd_access" if hako_cmd == true || hako_cmd.to_s.start_with?("required")
    signals
  end

  def validate_foundation_contract(value, requirements, execution_environment, label:)
    if value.nil?
      return [] unless requirements.is_a?(Hash) && !requirements.empty?

      return ["#{label}: foundation_requirements must be classified by foundation_contract"]
    end
    unless value.is_a?(Hash) && value.keys.to_set == Set.new(%w[mode reason])
      return ["#{label}: foundation_contract must contain exactly mode and reason"]
    end

    mode = value["mode"]
    reason = value["reason"]
    errors = []
    unless %w[required not_required].include?(mode)
      errors << "#{label}: foundation_contract.mode must be required or not_required"
    end
    unless non_empty_string?(reason)
      errors << "#{label}: foundation_contract.reason must be a non-empty string"
    end

    workspace_mode = execution_environment.is_a?(Hash) ? execution_environment.dig("workspace", "mode") : nil
    has_requirements = requirements.is_a?(Hash) && !requirements.empty?
    if mode == "required"
      errors << "#{label}: required Foundation contract must define non-empty foundation_requirements" unless has_requirements
      errors << "#{label}: required Foundation contract must use execution_environment.workspace.mode managed" unless workspace_mode == "managed"
    elsif mode == "not_required"
      errors << "#{label}: not_required Foundation contract must not define foundation_requirements" if has_requirements
      errors << "#{label}: not_required Foundation contract must not use a managed Workspace" if workspace_mode == "managed"
      signals = foundation_usage_signals(execution_environment)
      unless signals.empty?
        errors << "#{label}: not_required Foundation contract conflicts with runtime signals: #{signals.join(', ')}"
      end
    end
    errors
  end

  def validate_requirements(value, label:, catalog_ids:)
    errors = []
    return errors if value.nil?

    unless value.is_a?(Hash) && !value.empty?
      return ["#{label}: foundation_requirements must be a non-empty mapping"]
    end

    value.each do |component_id, requirement|
      path = "#{label}: foundation_requirements.#{component_id}"
      unless component_id.is_a?(String) && catalog_ids.include?(component_id)
        errors << "#{path} references unknown component"
      end
      unless requirement.is_a?(Hash) && !requirement.empty?
        errors << "#{path} must be a non-empty mapping"
        next
      end

      unknown = requirement.keys.to_set - REQUIREMENT_FIELDS
      errors << "#{path} has unknown fields: #{unknown.to_a.sort.join(', ')}" unless unknown.empty?

      version = requirement["version"]
      capabilities = requirement["capabilities"]
      build_limits = requirement["build_limits"]
      if version.nil? && capabilities.nil? && build_limits.nil?
        errors << "#{path} must define version, capabilities, or build_limits"
      end
      errors.concat(validate_required_version(version, "#{path}.version")) unless version.nil?
      errors.concat(validate_required_capabilities(capabilities, "#{path}.capabilities")) unless capabilities.nil?
      errors.concat(validate_required_build_limits(build_limits, "#{path}.build_limits")) unless build_limits.nil?
    end

    errors
  end

  def validate_workspace_contract(execution_environment, requirements, label:)
    return [] if execution_environment.nil?
    return ["#{label}: execution_environment must be a mapping"] unless execution_environment.is_a?(Hash)

    workspace = execution_environment["workspace"]
    return [] if workspace.nil?
    unless workspace.is_a?(Hash) && workspace.keys == ["mode"]
      return ["#{label}: execution_environment.workspace must contain only mode"]
    end
    unless workspace["mode"] == "managed"
      return ["#{label}: execution_environment.workspace.mode must be managed"]
    end
    return [] if requirements.is_a?(Hash) && !requirements.empty?

    [
      "#{label}: managed Workspace Recipe must define non-empty foundation_requirements; " \
      "do not bypass this gate with component-local doctor/build/install commands"
    ]
  end

  def validate_receipt(value, label:)
    errors = []
    unless value.is_a?(Hash)
      return ["#{label}: receipt must be a mapping"]
    end

    missing = RECEIPT_FIELDS - value.keys.to_set
    unknown = value.keys.to_set - RECEIPT_FIELDS
    errors << "#{label}: missing required fields: #{missing.to_a.sort.join(', ')}" unless missing.empty?
    errors << "#{label}: unknown fields: #{unknown.to_a.sort.join(', ')}" unless unknown.empty?
    return errors unless missing.empty?

    errors << "#{label}: schema_version must be 1" unless value["schema_version"] == 1
    errors.concat(validate_exact_string_mapping(
      value["component"],
      "#{label}: component",
      %w[id version source_revision]
    ))
    errors.concat(validate_exact_string_mapping(
      value["platform"],
      "#{label}: platform",
      %w[os architecture toolchain]
    ))
    errors.concat(validate_exact_string_mapping(
      value["install"],
      "#{label}: install",
      %w[prefix]
    ))
    errors.concat(validate_installed_capabilities(value["capabilities"], "#{label}: capabilities"))
    errors.concat(validate_installed_build_limits(value["build_limits"], "#{label}: build_limits"))
    errors.concat(validate_dependencies(value["dependencies"], "#{label}: dependencies"))
    errors.concat(validate_artifacts(value["artifacts"], "#{label}: artifacts"))

    resolved_manifest = value["resolved_manifest"]
    unless relative_install_path?(resolved_manifest)
      errors << "#{label}: resolved_manifest must be a relative install-prefix path"
    end

    errors
  end

  def validate_required_version(value, path)
    unless value.is_a?(Hash) && value.keys == ["min"]
      return ["#{path} must contain only min"]
    end
    minimum = value["min"]
    return [] if minimum.is_a?(String) && DOTTED_NUMERIC_VERSION.match?(minimum)

    ["#{path}.min must be a dotted numeric version"]
  end

  def validate_required_capabilities(value, path)
    return ["#{path} must be a non-empty mapping"] unless value.is_a?(Hash) && !value.empty?

    value.each_with_object([]) do |(key, enabled), errors|
      errors << "#{path}.#{key} has invalid capability name" unless identifier?(key)
      errors << "#{path}.#{key} must be boolean" unless enabled == true || enabled == false
    end
  end

  def validate_required_build_limits(value, path)
    return ["#{path} must be a non-empty mapping"] unless value.is_a?(Hash) && !value.empty?

    value.each_with_object([]) do |(key, constraint), errors|
      errors << "#{path}.#{key} has invalid build-limit name" unless identifier?(key)
      unless constraint.is_a?(Hash) && constraint.keys == ["min"]
        errors << "#{path}.#{key} must contain only min"
        next
      end
      min = constraint["min"]
      errors << "#{path}.#{key}.min must be a positive integer" unless positive_integer?(min)
    end
  end

  def validate_installed_capabilities(value, path)
    return ["#{path} must be a mapping"] unless value.is_a?(Hash)

    value.each_with_object([]) do |(key, enabled), errors|
      errors << "#{path}.#{key} has invalid capability name" unless identifier?(key)
      errors << "#{path}.#{key} must be boolean" unless enabled == true || enabled == false
    end
  end

  def validate_installed_build_limits(value, path)
    return ["#{path} must be a mapping"] unless value.is_a?(Hash)

    value.each_with_object([]) do |(key, installed), errors|
      errors << "#{path}.#{key} has invalid build-limit name" unless identifier?(key)
      errors << "#{path}.#{key} must be a positive integer" unless positive_integer?(installed)
    end
  end

  def validate_dependencies(value, path)
    return ["#{path} must be a mapping"] unless value.is_a?(Hash)

    allowed = Set.new(%w[version source_revision build_limits])
    value.each_with_object([]) do |(component_id, dependency), errors|
      dependency_path = "#{path}.#{component_id}"
      unless component_id.is_a?(String) && !component_id.empty?
        errors << "#{dependency_path} has invalid component id"
      end
      unless dependency.is_a?(Hash)
        errors << "#{dependency_path} must be a mapping"
        next
      end
      unknown = dependency.keys.to_set - allowed
      errors << "#{dependency_path} has unknown fields: #{unknown.to_a.sort.join(', ')}" unless unknown.empty?
      if dependency.empty?
        errors << "#{dependency_path} must not be empty"
        next
      end
      %w[version source_revision].each do |field|
        next unless dependency.key?(field)
        errors << "#{dependency_path}.#{field} must be a non-empty string" unless non_empty_string?(dependency[field])
      end
      if dependency.key?("build_limits")
        errors.concat(validate_installed_build_limits(dependency["build_limits"], "#{dependency_path}.build_limits"))
      end
    end
  end

  def validate_artifacts(value, path)
    return ["#{path} must be a non-empty list"] unless value.is_a?(Array) && !value.empty?

    value.each_with_index.each_with_object([]) do |(artifact, index), errors|
      artifact_path = "#{path}[#{index}]"
      unless artifact.is_a?(Hash) && artifact.keys.to_set == Set.new(%w[path kind])
        errors << "#{artifact_path} must contain exactly path and kind"
        next
      end
      errors << "#{artifact_path}.path must be a relative install-prefix path" unless relative_install_path?(artifact["path"])
      errors << "#{artifact_path}.kind must be a non-empty string" unless non_empty_string?(artifact["kind"])
    end
  end

  def validate_exact_string_mapping(value, path, fields)
    expected = fields.to_set
    unless value.is_a?(Hash)
      return ["#{path} must be a mapping"]
    end

    errors = []
    missing = expected - value.keys.to_set
    unknown = value.keys.to_set - expected
    errors << "#{path} missing fields: #{missing.to_a.sort.join(', ')}" unless missing.empty?
    errors << "#{path} has unknown fields: #{unknown.to_a.sort.join(', ')}" unless unknown.empty?
    fields.each do |field|
      next unless value.key?(field)
      errors << "#{path}.#{field} must be a non-empty string" unless non_empty_string?(value[field])
    end
    errors
  end

  def relative_install_path?(value)
    return false unless non_empty_string?(value)

    path = Pathname.new(value)
    !path.absolute? && !path.each_filename.any? { |part| part == ".." }
  end

  def identifier?(value)
    value.is_a?(String) && IDENTIFIER.match?(value)
  end

  def positive_integer?(value)
    value.is_a?(Integer) && !value.is_a?(TrueClass) && !value.is_a?(FalseClass) && value.positive?
  end

  def non_empty_string?(value)
    value.is_a?(String) && !value.empty?
  end
end
