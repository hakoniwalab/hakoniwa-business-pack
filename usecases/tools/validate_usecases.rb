#!/usr/bin/env ruby
# frozen_string_literal: true

require "set"
require "yaml"

ROOT = File.expand_path("../..", __dir__)
USECASES_DIR = File.join(ROOT, "usecases")
EXAMPLES_DIR = File.join(USECASES_DIR, "examples")
CATALOG_DIR = File.join(ROOT, "catalog", "components")
RECIPES_DIR = File.join(ROOT, "recipes", "examples")

schema = YAML.load_file(File.join(USECASES_DIR, "schema.yaml"))
required_fields = schema.fetch("required_fields")
allowed_audiences = schema
  .fetch("controlled_fields")
  .fetch("audience.primary")
  .fetch("values")
  .to_set
allowed_feasibility = schema.fetch("status").fetch("feasibility").fetch("allowed").to_set
allowed_validation = schema.fetch("status").fetch("validation").fetch("allowed").to_set
allowed_confidence = schema.fetch("status").fetch("confidence").fetch("allowed").to_set

catalog_ids = Dir[File.join(CATALOG_DIR, "*.yaml")].map do |path|
  YAML.load_file(path).fetch("id")
end.to_set

recipe_ids = Dir[File.join(RECIPES_DIR, "*.yaml")].map do |path|
  YAML.load_file(path).fetch("id")
end.to_set

usecase_paths = Dir[File.join(EXAMPLES_DIR, "*.yaml")].sort
errors = []
warnings = []
seen_ids = Set.new

usecase_paths.each do |path|
  data = YAML.load_file(path)
  label = File.basename(path)
  id = data["id"]

  missing = required_fields.reject { |field| data.key?(field) }
  errors << "#{label}: missing required fields: #{missing.join(', ')}" unless missing.empty?

  if id.nil? || id.empty?
    errors << "#{label}: id must be present"
  else
    errors << "#{label}: file name must match id #{id}" unless File.basename(path, ".yaml") == id
    errors << "#{label}: duplicate usecase id #{id}" unless seen_ids.add?(id)
  end

  audience = data["audience"] || {}
  primary_audiences = Array(audience["primary"])
  errors << "#{label}: audience.primary must not be empty" if primary_audiences.empty?
  primary_audiences.each do |audience_id|
    errors << "#{label}: unknown audience #{audience_id}" unless allowed_audiences.include?(audience_id)
  end

  status = data["status"] || {}
  unless allowed_feasibility.include?(status["feasibility"])
    errors << "#{label}: invalid status.feasibility #{status['feasibility']}"
  end
  unless allowed_validation.include?(status["validation"])
    errors << "#{label}: invalid status.validation #{status['validation']}"
  end
  unless allowed_confidence.include?(status["confidence"])
    errors << "#{label}: invalid status.confidence #{status['confidence']}"
  end

  Array(data["realized_by"]).each do |reference|
    recipe_id = reference["recipe_id"]
    if recipe_id.nil? || recipe_id.empty?
      errors << "#{label}: realized_by entry missing recipe_id"
    elsif !recipe_ids.include?(recipe_id)
      errors << "#{label}: realized_by references unknown recipe #{recipe_id}"
    end
  end

  supported_component_ids = Array(data["supported_by"]).map do |reference|
    component_id = reference["component_id"]
    if component_id.nil? || component_id.empty?
      errors << "#{label}: supported_by entry missing component_id"
    elsif !catalog_ids.include?(component_id)
      errors << "#{label}: supported_by references unknown component #{component_id}"
    end
    component_id
  end.compact

  duplicates = supported_component_ids.tally.select { |_component_id, count| count > 1 }.keys
  warnings << "#{label}: duplicate supported_by components: #{duplicates.join(', ')}" unless duplicates.empty?
end

index = YAML.load_file(File.join(USECASES_DIR, "index.yaml"))
index_entries = Array(index["usecases"])
index_ids = Set.new

index_entries.each do |entry|
  id = entry["id"]
  path = entry["path"]
  label = "index.yaml"

  errors << "#{label}: entry missing id" if id.nil? || id.empty?
  errors << "#{label}: duplicate id #{id}" if id && !index_ids.add?(id)

  if path.nil? || path.empty?
    errors << "#{label}: #{id || '<unknown>'} missing path"
    next
  end

  full_path = File.join(USECASES_DIR, path)
  unless File.file?(full_path)
    errors << "#{label}: #{id} references missing file #{path}"
    next
  end

  referenced = YAML.load_file(full_path)
  errors << "#{label}: #{id} path points to id #{referenced['id']}" unless referenced["id"] == id

  status = referenced["status"] || {}
  errors << "#{label}: #{id} feasibility differs from example" unless entry["feasibility"] == status["feasibility"]
  errors << "#{label}: #{id} validation differs from example" unless entry["validation"] == status["validation"]
end

missing_from_index = seen_ids - index_ids
extra_in_index = index_ids - seen_ids
errors << "index.yaml: missing usecases: #{missing_from_index.to_a.sort.join(', ')}" unless missing_from_index.empty?
errors << "index.yaml: unknown usecases: #{extra_in_index.to_a.sort.join(', ')}" unless extra_in_index.empty?

warnings.each { |message| warn "warning: #{message}" }

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "usecases valid: usecases=#{usecase_paths.length}, audiences=#{allowed_audiences.length}, warnings=#{warnings.length}"
