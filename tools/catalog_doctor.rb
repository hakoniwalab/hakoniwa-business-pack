#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "json"
require "open3"
require "optparse"
require "rbconfig"
require "yaml"

ROOT = File.expand_path("..", __dir__)
COMPONENTS_DIR = File.join(ROOT, "catalog", "components")
RECIPES_DIR = File.join(ROOT, "recipes", "examples")


def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  # Older Psych (e.g. macOS system Ruby) does not accept permitted_classes.
  YAML.load_file(path)
end


def current_platform
  host_os = RbConfig::CONFIG.fetch("host_os")
  return "windows" if host_os.match?(/mswin|mingw|cygwin/i)
  return "macos" if host_os.match?(/darwin/i)

  "linux"
end


def component_entries
  Dir[File.join(COMPONENTS_DIR, "*.yaml")].sort.to_h do |path|
    entry = load_yaml_file(path)
    [entry.fetch("id"), entry]
  end
end


def recipe_component_ids(recipe_id)
  path = File.join(RECIPES_DIR, "#{recipe_id}.yaml")
  raise "recipe not found: #{recipe_id}" unless File.file?(path)

  recipe = load_yaml_file(path)
  Array(recipe["components"]).filter_map do |component|
    component.is_a?(Hash) ? component["id"] : component
  end.uniq
end


def select_component_ids(entries, options)
  if options[:component]
    raise "component not found: #{options[:component]}" unless entries.key?(options[:component])
    return [options[:component]]
  end

  if options[:category]
    ids = entries.values
                 .select { |entry| entry.dig("category", "primary") == options[:category] }
                 .map { |entry| entry.fetch("id") }
                 .sort
    raise "no components found for category: #{options[:category]}" if ids.empty?
    return ids
  end

  return recipe_component_ids(options[:recipe]) if options[:recipe]
  return entries.keys.sort if options[:all]

  raise "select one of --component, --category, --recipe, or --all"
end


def run_check(check, repository_dir, dry_run:)
  command = Array(check.fetch("command"))
  result = {
    "id" => check.fetch("id"),
    "kind" => check.fetch("kind"),
    "command" => command,
    "status" => "planned"
  }

  return result if dry_run

  stdout, stderr, process_status = Open3.capture3(*command, chdir: repository_dir)
  result["status"] = process_status.success? ? "pass" : "fail"
  result["exit_code"] = process_status.exitstatus
  result["stdout"] = stdout unless stdout.empty?
  result["stderr"] = stderr unless stderr.empty?
  result
rescue Errno::ENOENT => e
  result["status"] = "fail"
  result["error"] = e.message
  result
end


def verify_component(entry, platform:, workspace:, dry_run:)
  repository_name = entry.dig("repository", "name")
  result = {
    "component" => entry.fetch("id"),
    "repository" => repository_name,
    "platform" => platform,
    "status" => "unknown",
    "checks" => []
  }

  checks = Array(entry["runtime_checks"]).select do |check|
    Array(check["platforms"]).include?(platform)
  end

  if checks.empty?
    result["reason"] = "no runtime check declared for this platform"
    return result
  end

  repository_dir = File.expand_path(repository_name, workspace)
  result["repository_dir"] = repository_dir
  unless Dir.exist?(repository_dir)
    result["status"] = "fail"
    result["reason"] = "repository is not present in the workspace"
    return result
  end

  result["checks"] = checks.map do |check|
    run_check(check, repository_dir, dry_run: dry_run)
  end

  result["status"] = if dry_run
                       "planned"
                     elsif result["checks"].all? { |check| check["status"] == "pass" }
                       "pass"
                     else
                       "fail"
                     end
  result
end


def print_text(results, workspace:, platform:)
  puts "Hakoniwa Business Pack runtime verification"
  puts "platform:  #{platform}"
  puts "workspace: #{workspace}"
  puts

  results.each do |result|
    puts "[#{result['status'].upcase}] #{result['component']}"
    if result["reason"]
      puts "  #{result['reason']}"
      next
    end

    result["checks"].each do |check|
      command = check["command"].join(" ")
      puts "  [#{check['status'].upcase}] #{check['id']}: #{command}"
      puts check["stdout"].lines.map { |line| "    #{line}" } if check["stdout"]
      warn check["stderr"].lines.map { |line| "    #{line}" } if check["stderr"]
      puts "    #{check['error']}" if check["error"]
    end
  end

  counts = results.group_by { |result| result["status"] }.transform_values(&:length)
  puts
  puts "Summary: pass=#{counts.fetch('pass', 0)} fail=#{counts.fetch('fail', 0)} unknown=#{counts.fetch('unknown', 0)} planned=#{counts.fetch('planned', 0)}"
end

options = {
  workspace: File.expand_path("..", ROOT),
  dry_run: false,
  json: false,
  strict: false
}

parser = OptionParser.new do |opts|
  opts.banner = "Usage: ruby tools/catalog_doctor.rb [selector] [options]"
  opts.on("--component ID", "Verify one catalog component") { |value| options[:component] = value }
  opts.on("--category NAME", "Verify all components in a primary catalog category") { |value| options[:category] = value }
  opts.on("--recipe ID", "Verify components referenced by a recipe") { |value| options[:recipe] = value }
  opts.on("--all", "Verify all catalog components") { options[:all] = true }
  opts.on("--workspace DIR", "Directory containing sibling Hakoniwa repositories") { |value| options[:workspace] = File.expand_path(value) }
  opts.on("--dry-run", "Show selected checks without executing them") { options[:dry_run] = true }
  opts.on("--json", "Emit machine-readable JSON") { options[:json] = true }
  opts.on("--strict", "Treat unknown/unverified components as failure") { options[:strict] = true }
  opts.on("-h", "--help", "Show this help") do
    puts opts
    exit 0
  end
end

begin
  parser.parse!
  selectors = %i[component category recipe all].count { |key| options[key] }
  raise "select exactly one of --component, --category, --recipe, or --all" unless selectors == 1

  entries = component_entries
  ids = select_component_ids(entries, options)
  missing = ids.reject { |id| entries.key?(id) }
  raise "recipe references uncataloged components: #{missing.join(', ')}" unless missing.empty?

  platform = current_platform
  results = ids.map do |id|
    verify_component(
      entries.fetch(id),
      platform: platform,
      workspace: options[:workspace],
      dry_run: options[:dry_run]
    )
  end

  if options[:json]
    puts JSON.pretty_generate(
      "platform" => platform,
      "workspace" => options[:workspace],
      "results" => results
    )
  else
    print_text(results, workspace: options[:workspace], platform: platform)
  end

  failed = results.any? { |result| result["status"] == "fail" }
  unknown = results.any? { |result| result["status"] == "unknown" }
  exit 1 if failed || (options[:strict] && unknown)
rescue OptionParser::ParseError, RuntimeError, KeyError => e
  warn "error: #{e.message}"
  warn parser
  exit 2
end
