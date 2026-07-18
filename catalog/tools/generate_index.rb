#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "yaml"

ROOT = File.expand_path("..", __dir__)
COMPONENTS_DIR = File.join(ROOT, "components")
INDEX_PATH = File.join(ROOT, "index.yaml")

def compact_component(path)
  data = YAML.load_file(path)
  {
    "id" => data.fetch("id"),
    "name" => data.fetch("name"),
    "category" => data.fetch("category").fetch("primary"),
    "secondary_categories" => Array(data.fetch("category")["secondary"]),
    "maturity" => data.fetch("status").fetch("maturity"),
    "distribution" => data.fetch("distribution", { "channel" => "unknown" }).fetch("channel", "unknown"),
    "summary" => data.fetch("summary"),
    "recipe_roles" => Array(data["recipe_roles"]).map { |role| role.fetch("role") },
    "connects_to" => Array(data["connects_to"]).map { |edge| edge.fetch("component_id") },
    "tags" => Array(data["tags"]),
    "catalog_path" => File.join("components", File.basename(path))
  }
end

components = Dir[File.join(COMPONENTS_DIR, "*.yaml")]
  .sort
  .map { |path| compact_component(path) }

index = {
  "index_version" => 0.1,
  "generated_at" => Date.today.iso8601,
  "generated_by" => "catalog/tools/generate_index.rb",
  "description" => "Lightweight search index for Hakoniwa component catalogs.",
  "components" => components
}

File.write(INDEX_PATH, "#{index.to_yaml.sub(/^---\n/, "")}\n")
puts "generated #{INDEX_PATH} (components=#{components.length})"

