#!/usr/bin/env ruby
# frozen_string_literal: true

require "minitest/autorun"
require_relative "validate_recipes"

class ExecutionContextValidationTest < Minitest::Test
  def context
    {
      "location" => "host",
      "terminal" => "host-hako",
      "cwd" => "Business Pack root",
      "environment" => "Foundation Workspace (hako)"
    }
  end

  def test_legacy_and_contextual_steps_are_valid
    assert_empty validate_execution_contexts(
      { "steps" => [{ "command" => "python legacy.py" }] }, label: "test"
    )
    %w[host container].each do |location|
      [true, false].each do |transition|
        value = context.merge("location" => location, "shell_transition" => transition)
        %w[prerequisites steps].each do |section|
          assert_empty validate_execution_contexts(
            { section => [{ "execution_context" => value }] }, label: "test"
          )
        end
      end
    end
    assert_empty validate_execution_contexts(
      { "steps" => [{ "execution_context" => context }] }, label: "test"
    )
  end

  def test_invalid_context_reports_the_step_path
    invalid = [
      nil, {}, [], "host",
      context.merge("location" => "remote"),
      context.merge("terminal" => " "),
      context.merge("cwd" => 42),
      context.reject { |key, _| key == "environment" },
      context.merge("shell_transition" => "false"),
      context.merge("shell_transition" => 1),
      context.merge("working_directory" => ".")
    ]
    %w[prerequisites steps].each do |section|
      invalid.each do |value|
        errors = validate_execution_contexts(
          { section => [{ "execution_context" => value }] }, label: "test"
        )
        refute_empty errors, value.inspect
        assert errors.all? { |error| error.start_with?("test: demo.#{section}[0].execution_context") }
      end
    end
  end
end
