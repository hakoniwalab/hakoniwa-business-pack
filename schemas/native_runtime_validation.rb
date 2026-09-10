# frozen_string_literal: true

require "set"

module NativeRuntimeValidation
  module_function

  def non_empty_string?(value)
    value.is_a?(String) && !value.empty?
  end

  def string_list?(value)
    value.is_a?(Array) && value.all? { |item| non_empty_string?(item) }
  end

  def require_fields(errors, value, fields, prefix)
    Array(fields).each do |field|
      errors << "#{prefix} missing #{field}" unless value.key?(field)
    end
  end

  def validate_catalog(value, schema, label:)
    return [] if value.nil?

    errors = []
    unless value.is_a?(Hash)
      return ["#{label}: native_runtime must be a mapping"]
    end
    require_fields(
      errors,
      value,
      schema.dig("catalog_declaration", "required_fields"),
      label
    )
    supported = schema.dig("catalog_declaration", "supported_schema_versions") || []
    unless supported.include?(value["schema_version"])
      errors << "#{label}: unsupported native_runtime.schema_version #{value['schema_version'].inspect}"
    end
    profiles = value["profiles"]
    unless profiles.is_a?(Hash) && !profiles.empty?
      errors << "#{label}: native_runtime.profiles must be a non-empty mapping"
      return errors
    end

    allowed_platforms = Set.new(schema.dig("controlled_values", "platforms") || [])
    allowed_inspectors = Set.new(schema.dig("controlled_values", "dependency_inspectors") || [])
    profiles.each do |profile_id, profile|
      prefix = "#{label}: native_runtime.profiles.#{profile_id}"
      unless non_empty_string?(profile_id) && profile.is_a?(Hash)
        errors << "#{prefix} must be a named mapping"
        next
      end
      require_fields(
        errors,
        profile,
        schema.dig("catalog_declaration", "profile", "required_fields"),
        prefix
      )
      unless non_empty_string?(profile["distribution_release"])
        errors << "#{prefix}.distribution_release must be a non-empty string"
      end
      if profile.key?("source_contract") && !non_empty_string?(profile["source_contract"])
        errors << "#{prefix}.source_contract must be a non-empty relative path"
      end

      managed = profile["managed_runtimes"]
      unless managed.is_a?(Hash)
        errors << "#{prefix}.managed_runtimes must be a mapping"
      else
        managed.each do |runtime_id, runtime|
          runtime_prefix = "#{prefix}.managed_runtimes.#{runtime_id}"
          unless runtime.is_a?(Hash)
            errors << "#{runtime_prefix} must be a mapping"
            next
          end
          require_fields(
            errors,
            runtime,
            schema.dig("catalog_declaration", "profile", "managed_runtime", "required_fields"),
            runtime_prefix
          )
          unless runtime["required"] == true || runtime["required"] == false
            errors << "#{runtime_prefix}.required must be boolean"
          end
          unless non_empty_string?(runtime["version_file"])
            errors << "#{runtime_prefix}.version_file must be a non-empty path"
          end
          runtime_platforms = runtime["platforms"]
          unless runtime_platforms.is_a?(Hash) && !runtime_platforms.empty?
            errors << "#{runtime_prefix}.platforms must be a non-empty mapping"
            next
          end
          runtime_platforms.each do |platform, platform_runtime|
            errors << "#{runtime_prefix}.platforms has unknown platform #{platform}" unless allowed_platforms.include?(platform)
            if platform_runtime.is_a?(Hash)
              require_fields(
                errors,
                platform_runtime,
                schema.dig("catalog_declaration", "profile", "managed_runtime", "platform_required_fields"),
                "#{runtime_prefix}.platforms.#{platform}"
              )
            end
            library = platform_runtime.is_a?(Hash) ? platform_runtime["library"] : nil
            unless non_empty_string?(library) && library.include?("{version}")
              errors << "#{runtime_prefix}.platforms.#{platform}.library must contain {version}"
            end
          end
        end
      end

      platforms = profile["platforms"]
      unless platforms.is_a?(Hash) && !platforms.empty?
        errors << "#{prefix}.platforms must be a non-empty mapping"
        next
      end
      platforms.each do |platform, platform_value|
        platform_prefix = "#{prefix}.platforms.#{platform}"
        errors << "#{prefix}.platforms has unknown platform #{platform}" unless allowed_platforms.include?(platform)
        unless platform_value.is_a?(Hash)
          errors << "#{platform_prefix} must be a mapping"
          next
        end
        require_fields(
          errors,
          platform_value,
          schema.dig("catalog_declaration", "profile", "platform", "required_fields"),
          platform_prefix
        )
        inspector = platform_value["dependency_inspector"]
        unless allowed_inspectors.include?(inspector)
          errors << "#{platform_prefix}.dependency_inspector is invalid: #{inspector.inspect}"
        end
        roles = platform_value["binary_roles"]
        unless roles.is_a?(Hash) && !roles.empty? && roles.all? { |role, path| non_empty_string?(role) && non_empty_string?(path) }
          errors << "#{platform_prefix}.binary_roles must be a non-empty string mapping"
        end
        unless string_list?(platform_value["required_libraries"])
          errors << "#{platform_prefix}.required_libraries must be a string list"
        end
      end
    end
    errors
  end

  def validate_recipe(value, catalog, schema, label:)
    return [] if value.nil?

    errors = []
    unless value.is_a?(Hash)
      return ["#{label}: native_runtime_requirements must be a mapping"]
    end
    require_fields(
      errors,
      value,
      schema.dig("recipe_requirement", "required_fields"),
      label
    )
    supported = schema.dig("recipe_requirement", "supported_schema_versions") || []
    unless supported.include?(value["schema_version"])
      errors << "#{label}: unsupported native_runtime_requirements.schema_version #{value['schema_version'].inspect}"
    end
    components = value["components"]
    unless components.is_a?(Hash) && !components.empty?
      errors << "#{label}: native_runtime_requirements.components must be a non-empty mapping"
      return errors
    end
    components.each do |component_id, requirement|
      prefix = "#{label}: native_runtime_requirements.components.#{component_id}"
      component = catalog[component_id]
      unless component
        errors << "#{prefix} references an unknown Catalog component"
        next
      end
      unless requirement.is_a?(Hash)
        errors << "#{prefix} must be a mapping"
        next
      end
      require_fields(
        errors,
        requirement,
        schema.dig("recipe_requirement", "component", "required_fields"),
        prefix
      )
      profile_id = requirement["profile"]
      profile = component.dig("native_runtime", "profiles", profile_id)
      unless profile
        errors << "#{prefix}.profile is not declared by the Catalog: #{profile_id.inspect}"
        next
      end
      required = requirement["required_roles"]
      optional = requirement["optional_roles"]
      errors << "#{prefix}.required_roles must be a non-empty string list" unless string_list?(required) && !required.empty?
      errors << "#{prefix}.optional_roles must be a string list" unless string_list?(optional)
      overlap = Set.new(Array(required)) & Set.new(Array(optional))
      errors << "#{prefix} roles cannot be both required and optional: #{overlap.to_a.sort.join(', ')}" unless overlap.empty?
      declared_by_platform = profile.fetch("platforms", {}).values.map do |platform|
        Set.new(platform.is_a?(Hash) ? platform.fetch("binary_roles", {}).keys : [])
      end
      Array(required).concat(Array(optional)).uniq.each do |role|
        unless declared_by_platform.all? { |roles| roles.include?(role) }
          errors << "#{prefix} role is not available on every profile platform: #{role}"
        end
      end
    end
    errors
  end
end
