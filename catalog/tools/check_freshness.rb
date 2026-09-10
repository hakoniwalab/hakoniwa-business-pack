#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "json"
require "net/http"
require "optparse"
require "uri"
require "yaml"

ROOT = File.expand_path("..", __dir__)
COMPONENTS_DIR = File.join(ROOT, "components")
DEFAULT_API_URL = "https://api.github.com"


def load_yaml_file(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  YAML.load_file(path)
end


def classify_compare(ahead_by:, behind_by:)
  return ["current", 0] if ahead_by.zero? && behind_by.zero?
  return ["behind", ahead_by] if ahead_by.positive? && behind_by.zero?
  return ["catalog-ahead", behind_by] if ahead_by.zero? && behind_by.positive?

  ["diverged", ahead_by]
end

class GitHubClient
  def initialize(api_url:, token: nil)
    @api_url = api_url.sub(%r{/$}, "")
    @token = token
  end

  def get_json(path)
    uri = URI("#{@api_url}#{path}")
    request = Net::HTTP::Get.new(uri)
    request["Accept"] = "application/vnd.github+json"
    request["User-Agent"] = "hakoniwa-business-pack-catalog-freshness"
    request["Authorization"] = "Bearer #{@token}" if @token && !@token.empty?

    response = Net::HTTP.start(uri.hostname, uri.port, use_ssl: uri.scheme == "https") do |http|
      http.request(request)
    end

    return JSON.parse(response.body) if response.is_a?(Net::HTTPSuccess)

    message = begin
      JSON.parse(response.body)["message"]
    rescue JSON::ParserError
      response.message
    end
    raise "HTTP #{response.code}: #{message}"
  end
end


def check_component(data, client)
  id = data.fetch("id")
  owner = data.dig("repository", "owner")
  repo = data.dig("repository", "name")
  revision = data.dig("verification", "source_revision")
  ref = data.dig("verification", "source_ref") || "main"

  unless owner && repo && revision
    return { "id" => id, "status" => "uncheckable", "detail" => "missing repository or verification metadata" }
  end

  head = client.get_json("/repos/#{URI.encode_www_form_component(owner)}/#{URI.encode_www_form_component(repo)}/commits/#{URI.encode_www_form_component(ref)}").fetch("sha")
  return { "id" => id, "catalog_revision" => revision, "head_revision" => head, "status" => "current", "commits" => 0 } if head == revision

  compare = client.get_json("/repos/#{URI.encode_www_form_component(owner)}/#{URI.encode_www_form_component(repo)}/compare/#{URI.encode_www_form_component(revision)}...#{URI.encode_www_form_component(head)}")
  status, commits = classify_compare(ahead_by: compare.fetch("ahead_by"), behind_by: compare.fetch("behind_by"))
  {
    "id" => id,
    "catalog_revision" => revision,
    "head_revision" => head,
    "status" => status,
    "commits" => commits,
    "ahead_by" => compare.fetch("ahead_by"),
    "behind_by" => compare.fetch("behind_by")
  }
rescue StandardError => e
  {
    "id" => id,
    "catalog_revision" => revision,
    "status" => "unreachable",
    "detail" => e.message
  }
end


def print_table(results)
  widths = {
    id: ["COMPONENT".length, results.map { |r| r["id"].to_s.length }.max || 0].max,
    catalog: 12,
    head: 12,
    status: ["STATUS".length, results.map { |r| r["status"].to_s.length }.max || 0].max
  }

  format = "%-#{widths[:id]}s  %-#{widths[:catalog]}s  %-#{widths[:head]}s  %-#{widths[:status]}s  %s\n"
  printf(format, "COMPONENT", "CATALOG", "HEAD", "STATUS", "COMMITS/DETAIL")
  puts("-" * (widths.values.sum + 26))
  results.each do |result|
    catalog = result["catalog_revision"]&.slice(0, 12) || "-"
    head = result["head_revision"]&.slice(0, 12) || "-"
    detail = result.key?("commits") ? result["commits"].to_s : result["detail"].to_s
    printf(format, result["id"], catalog, head, result["status"], detail)
  end
end

if $PROGRAM_NAME == __FILE__
  options = { strict: false, json: false, api_url: ENV.fetch("GITHUB_API_URL", DEFAULT_API_URL) }
  parser = OptionParser.new do |opts|
    opts.banner = "Usage: ruby catalog/tools/check_freshness.rb [options] [component-id ...]"
    opts.on("--strict", "Exit non-zero when a catalog is stale, diverged, ahead, or unreachable") { options[:strict] = true }
    opts.on("--json", "Emit JSON instead of a table") { options[:json] = true }
    opts.on("--api-url URL", "GitHub API base URL (default: GITHUB_API_URL or api.github.com)") { |value| options[:api_url] = value }
  end
  component_ids = parser.parse(ARGV)

  paths = Dir[File.join(COMPONENTS_DIR, "*.yaml")].sort
  entries = paths.map { |path| load_yaml_file(path) }
  entries.select! { |entry| component_ids.include?(entry["id"]) } unless component_ids.empty?

  missing_ids = component_ids - entries.map { |entry| entry["id"] }
  unless missing_ids.empty?
    warn "unknown component id(s): #{missing_ids.join(', ')}"
    exit 2
  end

  client = GitHubClient.new(api_url: options[:api_url], token: ENV["GITHUB_TOKEN"])
  results = entries.map { |entry| check_component(entry, client) }

  if options[:json]
    puts JSON.pretty_generate(results)
  else
    print_table(results)
  end

  unhealthy = results.any? { |result| result["status"] != "current" }
  exit(options[:strict] && unhealthy ? 1 : 0)
end
