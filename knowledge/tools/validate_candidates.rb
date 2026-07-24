#!/usr/bin/env ruby
# frozen_string_literal: true

require "set"
require "yaml"
require "date"

def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  # Older Psych (e.g. macOS system Ruby) does not accept permitted_classes.
  YAML.load_file(path)
end

KNOWLEDGE_DIR = File.expand_path("..", __dir__)
CANDIDATE_DIR = File.join(KNOWLEDGE_DIR, "candidates")

ALLOWED_IMPLEMENTATION_STATUSES = %w[none open in_progress resolved wont_fix].to_set.freeze
ALLOWED_ISSUE_RELATIONS = %w[fix documentation validation follow_up].to_set.freeze
ALLOWED_ISSUE_STATUSES = %w[open closed unknown].to_set.freeze
ALLOWED_RESOLUTION_STATUSES = %w[pending fixed documented superseded wont_fix].to_set.freeze

candidate_paths = Dir[File.join(CANDIDATE_DIR, "*.yaml")].sort
errors = []
warnings = []
tracked = 0

candidate_paths.each do |path|
  label = File.basename(path)

  begin
    data = load_yaml_file(path)
  rescue StandardError => e
    errors << "#{label}: invalid YAML: #{e.message}"
    next
  end

  unless data.is_a?(Hash)
    errors << "#{label}: top-level YAML value must be a mapping"
    next
  end

  id = data["id"]
  errors << "#{label}: missing id" if id.nil? || id.to_s.empty?
  errors << "#{label}: file name must match id" if id && File.basename(path, ".yaml") != id

  tracking = data["tracking"]
  if tracking
    tracked += 1
    unless tracking.is_a?(Hash)
      errors << "#{label}: tracking must be a mapping"
      next
    end

    implementation_status = tracking["implementation_status"]
    unless ALLOWED_IMPLEMENTATION_STATUSES.include?(implementation_status)
      errors << "#{label}: invalid tracking.implementation_status #{implementation_status.inspect}"
    end

    issues = tracking["issues"]
    unless issues.is_a?(Array)
      errors << "#{label}: tracking.issues must be an array"
      issues = []
    end

    issues.each_with_index do |issue, index|
      prefix = "#{label}: tracking.issues[#{index}]"
      unless issue.is_a?(Hash)
        errors << "#{prefix} must be a mapping"
        next
      end

      repository = issue["repository"]
      number = issue["number"]
      relation = issue["relation"]
      status = issue["status"]

      errors << "#{prefix}.repository is required" if repository.nil? || repository.to_s.empty?
      errors << "#{prefix}.number must be a positive integer" unless number.is_a?(Integer) && number.positive?
      errors << "#{prefix}.relation is invalid: #{relation.inspect}" unless ALLOWED_ISSUE_RELATIONS.include?(relation)
      errors << "#{prefix}.status is invalid: #{status.inspect}" unless ALLOWED_ISSUE_STATUSES.include?(status)
    end
  end

  resolution = data["resolution"]
  next unless resolution

  unless resolution.is_a?(Hash)
    errors << "#{label}: resolution must be a mapping"
    next
  end

  resolution_status = resolution["status"]
  if resolution_status && !ALLOWED_RESOLUTION_STATUSES.include?(resolution_status)
    errors << "#{label}: invalid resolution.status #{resolution_status.inspect}"
  end

  if resolution.key?("verified") && ![true, false].include?(resolution["verified"])
    errors << "#{label}: resolution.verified must be true or false"
  end

  fixed_by = resolution["fixed_by"]
  if fixed_by && !fixed_by.is_a?(Array)
    errors << "#{label}: resolution.fixed_by must be an array"
    fixed_by = []
  end

  Array(fixed_by).each_with_index do |fix, index|
    prefix = "#{label}: resolution.fixed_by[#{index}]"
    unless fix.is_a?(Hash)
      errors << "#{prefix} must be a mapping"
      next
    end

    repository = fix["repository"]
    errors << "#{prefix}.repository is required" if repository.nil? || repository.to_s.empty?

    %w[issue pull_request].each do |field|
      value = fix[field]
      next if value.nil?

      errors << "#{prefix}.#{field} must be a positive integer or null" unless value.is_a?(Integer) && value.positive?
    end

    revision = fix["revision"]
    unless revision.nil? || (revision.is_a?(String) && !revision.empty?)
      errors << "#{prefix}.revision must be a non-empty string or null"
    end
  end

  if resolution["verified"] == true && resolution_status == "pending"
    warnings << "#{label}: resolution is verified but still pending"
  end
end

warnings.each { |message| warn "warning: #{message}" }

unless errors.empty?
  errors.each { |message| warn "error: #{message}" }
  exit 1
end

puts "knowledge candidates valid: candidates=#{candidate_paths.length}, tracked=#{tracked}, warnings=#{warnings.length}"
