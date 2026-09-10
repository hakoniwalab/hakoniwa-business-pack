#!/usr/bin/env ruby
# frozen_string_literal: true

ROOT = File.expand_path("../..", __dir__)

Dir.chdir(ROOT) do
  def run!(*command)
    puts "+ #{command.join(' ')}"
    return if system(*command)

    abort "catalog preflight failed: #{command.join(' ')}"
  end

  run!("ruby", "catalog/tools/validate_catalog.rb")
  run!("ruby", "catalog/tools/generate_index.rb")

  unless system("git", "diff", "--exit-code", "--", "catalog/index.yaml")
    abort <<~MESSAGE
      catalog preflight failed: catalog/index.yaml is stale.
      Review the generated diff, commit catalog/index.yaml, then rerun:
        ruby catalog/tools/preflight.rb
    MESSAGE
  end

  puts "catalog preflight passed"
end
