#!/usr/bin/env ruby
# frozen_string_literal: true

require "set"
require "yaml"
require "date"
require_relative "../../foundation/tools/validation"

def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  # Older Psych (e.g. macOS system Ruby) does not accept permitted_classes
  # and already loads Date safely by default.
  YAML.load_file(path)
end

def non_empty_string?(value)
  value.is_a?(String) && !value.empty?
end

def launcher_managed_recipe?(data)
  demo = data["demo"] || {}
  profiles = Array(demo["profiles"])
  return true if profiles.any? { |profile| profile.is_a?(Hash) && non_empty_string?(profile["launcher"]) }

  Array(demo["steps"]).any? do |step|
    next false unless step.is_a?(Hash)

    command = step["command"].to_s
    command.include?("hako_launcher") || command.match?(%r{(^|/)launch\.bash(?:\s|$)})
  end
end

def validate_cleanup(value, label:)
  errors = []
  unless value.is_a?(Hash) && !value.empty?
    return ["#{label}: demo.cleanup must be a non-empty mapping"]
  end

  %w[expected_behavior verification_checks cautions].each do |field|
    items = value[field]
    unless items.is_a?(Array) && !items.empty? && items.all? { |item| non_empty_string?(item) }
      errors << "#{label}: demo.cleanup.#{field} must be a non-empty string list"
    end
  end

  if value.key?("owner") && !non_empty_string?(value["owner"])
    errors << "#{label}: demo.cleanup.owner must be a non-empty string"
  end
  if value.key?("provider") && !non_empty_string?(value["provider"])
    errors << "#{label}: demo.cleanup.provider must be a non-empty string"
  end
  if value.key?("mode") && !non_empty_string?(value["mode"])
    errors << "#{label}: demo.cleanup.mode must be a non-empty string"
  end
  if value.key?("target") && !non_empty_string?(value["target"])
    errors << "#{label}: demo.cleanup.target must be a non-empty string"
  end
  if value.key?("trigger") && !non_empty_string?(value["trigger"])
    errors << "#{label}: demo.cleanup.trigger must be a non-empty string"
  end

  if value.key?("normal")
    normal = value["normal"]
    unless normal.is_a?(Hash)
      errors << "#{label}: demo.cleanup.normal must be a mapping"
    else
      %w[interactive signal].each do |field|
        errors << "#{label}: demo.cleanup.normal.#{field} must be a non-empty string" unless non_empty_string?(normal[field])
      end
    end
  end

  errors
end

def validate_readiness(value, label:)
  return [] if value.nil?
  return ["#{label}: demo.readiness must be a non-empty mapping"] unless value.is_a?(Hash) && !value.empty?

  errors = []
  lifecycle = value["lifecycle_state"]
  unless lifecycle.is_a?(Hash)
    errors << "#{label}: demo.readiness.lifecycle_state must be a mapping"
  else
    %w[source required].each do |field|
      errors << "#{label}: demo.readiness.lifecycle_state.#{field} must be a non-empty string" unless non_empty_string?(lifecycle[field])
    end
    unless lifecycle["sufficient"] == true || lifecycle["sufficient"] == false
      errors << "#{label}: demo.readiness.lifecycle_state.sufficient must be boolean"
    end
  end

  checks = value["checks"]
  unless checks.is_a?(Array) && !checks.empty?
    errors << "#{label}: demo.readiness.checks must be a non-empty list"
  else
    checks.each_with_index do |check, index|
      unless check.is_a?(Hash)
        errors << "#{label}: demo.readiness.checks[#{index}] must be a mapping"
        next
      end
      %w[id kind target expected].each do |field|
        errors << "#{label}: demo.readiness.checks[#{index}].#{field} must be a non-empty string" unless non_empty_string?(check[field])
      end
    end
  end

  handoff = value["operator_handoff"]
  unless handoff.is_a?(Hash)
    errors << "#{label}: demo.readiness.operator_handoff must be a mapping"
  else
    %w[background explain_command_return].each do |field|
      unless handoff[field] == true || handoff[field] == false
        errors << "#{label}: demo.readiness.operator_handoff.#{field} must be boolean"
      end
    end
    actions = handoff["next_actions"]
    unless actions.is_a?(Array) && !actions.empty? && actions.all? { |item| non_empty_string?(item) }
      errors << "#{label}: demo.readiness.operator_handoff.next_actions must be a non-empty string list"
    end
  end
  errors
end

ROOT = File.expand_path("../..", __dir__)
CATALOG_DIR = File.join(ROOT, "catalog")
RECIPE_DIRS = [File.join(ROOT, "recipes", "examples")]

REQUIRED_FIELDS = %w[
  id
  title
  recipe_version
  goal
  feasibility
  validation
  constraints
  target_environment
  execution_environment
  components
  connections
  data_flow
  time_model
  artifact_sets
  artifacts
  missing_pieces
  demo
  expected_result
  source_catalogs
  source_artifacts
].freeze

schema = load_yaml_file(File.join(CATALOG_DIR, "schema.yaml")).fetch("controlled_fields")
catalog_paths = Dir[File.join(CATALOG_DIR, "components", "*.yaml")]
catalog = catalog_paths.to_h do |path|
  data = load_yaml_file(path)
  [data.fetch("id"), data]
end
catalog_ids = catalog.keys.to_set
allowed_roles = schema.fetch("recipe_roles.role").fetch("values").to_set
allowed_feasibility_statuses = %w[feasible partially_feasible not_feasible unknown].to_set
allowed_confidence = %w[high medium low].to_set
allowed_validation_statuses = %w[not_tested partially_verified verified blocked].to_set
allowed_step_statuses = %w[not_tested verified blocked skipped].to_set

recipe_paths = RECIPE_DIRS.flat_map { |dir| Dir[File.join(dir, "*.yaml")] }.sort
errors = []
warnings = []

recipe_paths.each do |path|
  data = load_yaml_file(path)
  label = File.basename(path)
  missing = REQUIRED_FIELDS.select { |key| !data.key?(key) }
  errors << "#{label}: missing required fields: #{missing.join(', ')}" unless missing.empty?

  errors << "#{label}: file name must match id" unless File.basename(path, ".yaml") == data["id"]

  feasibility = data["feasibility"] || {}
  unless allowed_feasibility_statuses.include?(feasibility["status"])
    errors << "#{label}: invalid feasibility.status #{feasibility['status']}"
  end
  unless allowed_confidence.include?(feasibility["confidence"])
    errors << "#{label}: invalid feasibility.confidence #{feasibility['confidence']}"
  end

  validation = data["validation"] || {}
  unless allowed_validation_statuses.include?(validation["status"])
    errors << "#{label}: invalid validation.status #{validation['status']}"
  end
  Array(validation["steps"]).each do |step|
    unless allowed_step_statuses.include?(step["status"])
      errors << "#{label}: invalid validation step status #{step['status']}"
    end
  end

  Array((data["artifact_sets"] || {}).values).each do |artifact_set|
    next if artifact_set.nil?

    unless allowed_validation_statuses.include?(artifact_set["status"])
      errors << "#{label}: invalid artifact_set status #{artifact_set['status']}"
    end
  end

  recipe_component_ids = Array(data["components"]).map { |component| component["id"] }
  recipe_component_ids.each do |id|
    errors << "#{label}: unknown component id #{id}" unless catalog_ids.include?(id)
  end

  Array(data["components"]).each do |component|
    Array(component["roles"]).each do |role|
      errors << "#{label}: invalid component role #{role}" unless allowed_roles.include?(role)
    end
  end

  Array(data["connections"]).each do |connection|
    %w[from to].each do |field|
      id = connection[field]
      errors << "#{label}: connection #{field} references unknown component #{id}" unless catalog_ids.include?(id)
    end
    contract = connection["contract"]
    if contract.nil?
      errors << "#{label}: connection #{connection['from']} -> #{connection['to']} missing contract"
      next
    end
    unless allowed_validation_statuses.include?(contract["status"])
      errors << "#{label}: invalid connection contract.status #{contract['status']}"
    end
  end

  Array(data["source_catalogs"]).each do |source|
    id = source["component_id"]
    errors << "#{label}: source_catalogs references unknown component #{id}" unless catalog_ids.include?(id)
  end

  source_catalog_ids = Array(data["source_catalogs"]).map { |source| source["component_id"] }.to_set
  missing_catalog_sources = recipe_component_ids.reject { |id| source_catalog_ids.include?(id) }
  unless missing_catalog_sources.empty?
    warnings << "#{label}: components missing from source_catalogs: #{missing_catalog_sources.join(', ')}"
  end

  demo = data["demo"] || {}
  errors.concat(validate_readiness(demo["readiness"], label: label))
  cleanup = demo["cleanup"]
  if launcher_managed_recipe?(data) && cleanup.nil?
    errors << "#{label}: long-running launcher Recipe must define demo.cleanup"
  elsif !cleanup.nil?
    errors.concat(validate_cleanup(cleanup, label: label))
  end

  errors.concat(
    FoundationValidation.validate_requirements(
      data["foundation_requirements"],
      label: label,
      catalog_ids: catalog_ids
    )
  )
end

warnings.each { |message| warn "warning: #{message}" }

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "recipes valid: recipes=#{recipe_paths.length}, warnings=#{warnings.length}"
