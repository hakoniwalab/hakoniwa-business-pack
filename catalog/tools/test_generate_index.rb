#!/usr/bin/env ruby
# frozen_string_literal: true

require "minitest/autorun"
require "open3"
require "rbconfig"

ROOT = File.expand_path("../..", __dir__)
GENERATOR = File.join(ROOT, "catalog", "tools", "generate_index.rb")
INDEX_PATH = File.join(ROOT, "catalog", "index.yaml")

class GenerateIndexTest < Minitest::Test
  def test_generated_index_is_lf_only
    original = File.binread(INDEX_PATH)

    begin
      stdout, stderr, status = Open3.capture3(
        RbConfig.ruby,
        GENERATOR,
        chdir: ROOT
      )

      assert status.success?, "generator failed: #{stdout}\n#{stderr}"
      generated = File.binread(INDEX_PATH)
      refute_includes generated, "\r\n"
      assert_includes generated, "\n"
    ensure
      File.binwrite(INDEX_PATH, original)
    end
  end
end
