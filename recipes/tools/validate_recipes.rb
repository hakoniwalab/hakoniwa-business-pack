#!/usr/bin/env ruby
# frozen_string_literal: true

require "set"
require "yaml"

ROOT = File.expand_path("../..", __dir__)
CATALOG_DIR = File.join(ROOT, "catalog")
RECIPE_DIRS = [File.join(ROOT, "recipes", "examples")]

REQUIRED_FIELDS = %w[
  id
  title
  recipe_version
  goal
  feasibility
  constraints
  target_environment
  components
  connections
  data_flow
  time_model
  artifacts
  missing_pieces
  demo
  expected_result
  source_catalogs
  source_artifacts
].freeze

schema = YAML.load_file(File.join(CATALOG_DIR, "schema.yaml")).fetch("controlled_fields")
catalog_paths = Dir[File.join(CATALOG_DIR, "components", "*.yaml")]
catalog = catalog_paths.to_h do |path|
  data = YAML.load_file(path)
  [data.fetch("id"), data]
end
catalog_ids = catalog.keys.to_set
allowed_roles = schema.fetch("recipe_roles.role").fetch("values").to_set

recipe_paths = RECIPE_DIRS.flat_map { |dir| Dir[File.join(dir, "*.yaml")] }.sort
errors = []
warnings = []

recipe_paths.each do |path|
  data = YAML.load_file(path)
  label = File.basename(path)
  missing = REQUIRED_FIELDS.select { |key| !data.key?(key) }
  errors << "#{label}: missing required fields: #{missing.join(', ')}" unless missing.empty?

  errors << "#{label}: file name must match id" unless File.basename(path, ".yaml") == data["id"]

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
end

warnings.each { |message| warn "warning: #{message}" }

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "recipes valid: recipes=#{recipe_paths.length}, warnings=#{warnings.length}"
