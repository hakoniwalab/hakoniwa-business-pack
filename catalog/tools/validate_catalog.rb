#!/usr/bin/env ruby
# frozen_string_literal: true

require "set"
require "yaml"
require "date"

ROOT = File.expand_path("..", __dir__)
COMPONENTS_DIR = File.join(ROOT, "components")
SCHEMA_PATH = File.join(ROOT, "schema.yaml")

REQUIRED_FIELDS = %w[
  id
  name
  repository
  distribution
  verification
  summary
  category
  status
  capabilities
  limitations
  interfaces
  dependencies
  connects_to
  recipe_roles
  typical_usecases
  demo_candidates
  source_refs
].freeze

def error(errors, path, message)
  errors << "#{path}: #{message}"
end

schema = YAML.load_file(SCHEMA_PATH, permitted_classes: [Date]).fetch("controlled_fields")
paths = Dir[File.join(COMPONENTS_DIR, "*.yaml")].sort
entries = paths.to_h { |path| [path, YAML.load_file(path, permitted_classes: [Date])] }
ids = entries.values.map { |entry| entry["id"] }
known_ids = ids.to_set
errors = []
warnings = []

duplicates = ids.group_by(&:itself).select { |_id, values| values.length > 1 }.keys
duplicates.each { |id| errors << "duplicate id: #{id}" }

entries.each do |path, data|
  label = File.basename(path)
  missing = REQUIRED_FIELDS.select { |key| !data.key?(key) }
  error(errors, label, "missing required fields: #{missing.join(', ')}") unless missing.empty?

  unless File.basename(path, ".yaml") == data["id"]
    error(errors, label, "file name must match id #{data['id'].inspect}")
  end

  primary = data.dig("category", "primary")
  unless schema.fetch("category.primary").fetch("values").include?(primary)
    error(errors, label, "invalid category.primary: #{primary.inspect}")
  end

  Array(data.dig("category", "secondary")).each do |value|
    unless schema.fetch("category.secondary").fetch("values").include?(value)
      error(errors, label, "invalid category.secondary: #{value.inspect}")
    end
  end

  maturity = data.dig("status", "maturity")
  unless schema.fetch("status.maturity").fetch("values").include?(maturity)
    error(errors, label, "invalid status.maturity: #{maturity.inspect}")
  end

  confidence = data.dig("status", "catalog_confidence")
  unless schema.fetch("status.catalog_confidence").fetch("values").include?(confidence)
    error(errors, label, "invalid status.catalog_confidence: #{confidence.inspect}")
  end

  visibility = data.dig("repository", "visibility")
  unless schema.fetch("repository.visibility").fetch("values").include?(visibility)
    error(errors, label, "invalid repository.visibility: #{visibility.inspect}")
  end

  channel = data.dig("distribution", "channel")
  unless schema.fetch("distribution.channel").fetch("values").include?(channel)
    error(errors, label, "invalid distribution.channel: #{channel.inspect}")
  end

  Array(data.dig("interfaces", "inputs")).each do |item|
    kind = item["kind"]
    unless schema.fetch("interfaces.kind").fetch("values").include?(kind)
      error(errors, label, "invalid interfaces.inputs.kind: #{kind.inspect}")
    end
  end

  Array(data.dig("interfaces", "outputs")).each do |item|
    kind = item["kind"]
    unless schema.fetch("interfaces.kind").fetch("values").include?(kind)
      error(errors, label, "invalid interfaces.outputs.kind: #{kind.inspect}")
    end
  end

  Array(data.fetch("dependencies", {}).fetch("required", [])).each do |item|
    type = item["type"]
    unless schema.fetch("dependencies.type").fetch("values").include?(type)
      error(errors, label, "invalid dependencies.required.type: #{type.inspect}")
    end
  end

  Array(data.fetch("dependencies", {}).fetch("optional", [])).each do |item|
    type = item["type"]
    unless schema.fetch("dependencies.type").fetch("values").include?(type)
      error(errors, label, "invalid dependencies.optional.type: #{type.inspect}")
    end
  end

  Array(data["connects_to"]).each do |edge|
    direction = edge["direction"]
    unless schema.fetch("connects_to.direction").fetch("values").include?(direction)
      error(errors, label, "invalid connects_to.direction: #{direction.inspect}")
    end

    target = edge["component_id"]
    next if known_ids.include?(target)

    warnings << "#{label}: connects_to target is not cataloged yet: #{target}"
  end

  Array(data["recipe_roles"]).each do |role|
    value = role["role"]
    unless schema.fetch("recipe_roles.role").fetch("values").include?(value)
      error(errors, label, "invalid recipe_roles.role: #{value.inspect}")
    end
  end
end

warnings.each { |message| warn "warning: #{message}" }

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "catalog valid: components=#{entries.length}, warnings=#{warnings.length}"

