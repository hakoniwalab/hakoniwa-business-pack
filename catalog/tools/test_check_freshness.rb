#!/usr/bin/env ruby
# frozen_string_literal: true

require "minitest/autorun"
require_relative "check_freshness"

class CheckFreshnessTest < Minitest::Test
  def test_current
    assert_equal ["current", 0], classify_compare(ahead_by: 0, behind_by: 0)
  end

  def test_catalog_is_behind_repository_head
    assert_equal ["behind", 4], classify_compare(ahead_by: 4, behind_by: 0)
  end

  def test_catalog_revision_is_ahead_of_ref
    assert_equal ["catalog-ahead", 2], classify_compare(ahead_by: 0, behind_by: 2)
  end

  def test_histories_diverged
    assert_equal ["diverged", 3], classify_compare(ahead_by: 3, behind_by: 1)
  end
end
