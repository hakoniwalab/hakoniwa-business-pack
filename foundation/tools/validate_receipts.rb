#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "yaml"
require_relative "validation"

ROOT = File.expand_path("../..", __dir__)
DEFAULT_GLOB = File.join(
  ROOT,
  "work",
  "foundation",
  "install",
  "share",
  "hakoniwa",
  "receipts",
  "*.yaml"
)

def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  YAML.load_file(path)
end

paths = ARGV.empty? ? Dir[DEFAULT_GLOB].sort : ARGV
errors = []

paths.each do |path|
  unless File.file?(path)
    errors << "#{path}: receipt file not found"
    next
  end

  begin
    receipt = load_yaml_file(path)
    errors.concat(FoundationValidation.validate_receipt(receipt, label: path))
  rescue Psych::SyntaxError => e
    errors << "#{path}: invalid YAML: #{e.message.lines.first.strip}"
  end
end

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "foundation receipts valid: receipts=#{paths.length}"
