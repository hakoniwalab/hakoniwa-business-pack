#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "json"
require "yaml"

def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  YAML.load_file(path)
end

unless ARGV.length == 1
  warn "usage: ruby recipes/tools/export_recipe_json.rb <recipe.yaml>"
  exit 2
end

path = File.expand_path(ARGV.fetch(0))
unless File.file?(path)
  warn "recipe not found: #{path}"
  exit 2
end

data = load_yaml_file(path)
unless data.is_a?(Hash)
  warn "recipe root must be a mapping: #{path}"
  exit 2
end

puts JSON.generate(data)
