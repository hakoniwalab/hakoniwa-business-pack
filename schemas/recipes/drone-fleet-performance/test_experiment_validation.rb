# frozen_string_literal: true

require "date"
require "minitest/autorun"
require "yaml"
require_relative "experiment_validation"

def load_experiment_yaml(path)
  YAML.load_file(path, permitted_classes: [Date])
rescue ArgumentError
  YAML.load_file(path)
end

class ExperimentValidationTest < Minitest::Test
  ROOT = File.expand_path("../../..", __dir__)
  SCHEMA = load_experiment_yaml(File.join(__dir__, "experiment.yaml"))

  def valid_multi_host
    {
      "version" => 1,
      "experiment" => {"id" => "drone-fleet-multi-host-legacy-256"},
      "scale" => {"drone_count" => 256, "drones_per_process" => "auto", "process_count" => 16},
      "runtime" => {
        "mode" => "native", "visualization" => true, "show_runner_real_time_sync" => false,
        "conductor" => {
          "implementation" => "hakoniwa-conductor-pro", "profile" => "legacy-distributed-10ms",
          "delta_time_usec" => 10_000, "max_delay_time_usec" => 20_000,
          "real_sleep_msec" => "unspecified", "simtime_publish_mode" => "legacy_simple"
        }
      },
      "scenario" => {"type" => "hakoniwa-word", "word" => "HAKONIWA"},
      "results" => {"enabled" => false, "directory" => "results"},
      "deployment" => {
        "mode" => "multi_host",
        "server_host" => "server",
        "transport" => {"type" => "tcp", "connection_initiator" => "client", "base_port" => 54_011},
        "hosts" => {
          "server" => {
            "role" => "server", "platform" => "macos", "execution_environment" => "native",
            "machine_id" => "srv-01", "node_id" => "srv-01-01",
            "address" => "192.168.2.100", "drone_count" => 128, "process_count" => 4,
            "global_start_index" => 0, "launcher_mode" => "activate-only"
          },
          "client" => {
            "role" => "client", "platform" => "linux", "execution_environment" => "wsl2",
            "node_id" => "cli-01", "connect_to" => "server",
            "drone_count" => 128, "process_count" => 12, "global_start_index" => 128,
            "launcher_mode" => "activate-only"
          }
        }
      },
      "visualization" => {
        "bridge_host" => "server", "viewer_host" => "server", "max_drones_per_packet" => 128,
        "bridge_subscriptions" => [0, 1],
        "publishers" => {
          "server" => {"chunk_index" => 0, "pdu_name" => "drone_visual_state_array_0", "transfer_policy" => "immediate-atomic"},
          "client" => {"chunk_index" => 1, "pdu_name" => "drone_visual_state_array_1", "transfer_policy" => "immediate-atomic"}
        }
      }
    }
  end

  def validate(value)
    ExperimentValidation.validate(value, SCHEMA, label: "fixture")
  end

  def test_current_drone_fleet_performance_inputs_are_valid
    paths = Dir[
      File.join(
        ROOT,
        "recipes",
        "experiments",
        "drone-fleet-performance",
        "**",
        "*.yaml"
      )
    ].sort
    refute_empty paths
    paths.each do |path|
      assert_empty validate(load_experiment_yaml(path)), path
    end
  end

  def test_valid_multi_host_contract
    assert_empty validate(valid_multi_host)
  end

  def test_rejects_unknown_controlled_field
    value = valid_multi_host
    value["deployment"]["transport"]["server_ip"] = "192.168.2.100"

    assert validate(value).any? { |error| error.include?("unknown fields: server_ip") }
  end

  def test_rejects_client_listener_address
    value = valid_multi_host
    value.dig("deployment", "hosts", "client")["address"] = "172.20.0.2"

    assert validate(value).any? { |error| error.include?("address is forbidden for role client") }
  end

  def test_rejects_more_than_one_server
    value = valid_multi_host
    client = value.dig("deployment", "hosts", "client")
    client["role"] = "server"
    client.delete("connect_to")
    client["address"] = "192.168.2.104"

    assert validate(value).any? { |error| error.include?("exactly one server") }
  end

  def test_rejects_gap_in_global_drone_partition
    value = valid_multi_host
    value.dig("deployment", "hosts", "client")["global_start_index"] = 129

    assert validate(value).any? { |error| error.include?("must be contiguous") }
  end

  def test_rejects_host_totals_that_disagree_with_scale
    value = valid_multi_host
    value.dig("deployment", "hosts", "client")["process_count"] = 11

    assert validate(value).any? { |error| error.include?("process_count total 15") }
  end

  def test_rejects_duplicate_visualization_chunks
    value = valid_multi_host
    value.dig("visualization", "publishers", "client")["chunk_index"] = 0

    assert validate(value).any? { |error| error.include?("chunk_index values must be unique") }
  end

  def test_rejects_conductor_profile_drift
    value = valid_multi_host
    value.dig("runtime", "conductor")["delta_time_usec"] = 1_000

    assert validate(value).any? { |error| error.include?("must be 10000") }
  end

  def test_rejects_bridge_subscription_drift
    value = valid_multi_host
    value.dig("visualization", "bridge_subscriptions").delete(1)

    assert validate(value).any? { |error| error.include?("must match publisher chunks") }
  end

  def test_scaling_allocation_accepts_auto_host_ranges
    value = load_experiment_yaml(
      File.join(
        ROOT,
        "recipes/experiments/drone-fleet-performance/multi-host-scaling.yaml"
      )
    )

    assert_empty validate(value)
    assert_equal 10, value.dig("runtime", "conductor", "real_sleep_msec")
    refute value.fetch("matrix").key?("conductor_real_sleep_msec")
  end

  def test_rejects_incomplete_allocation_order
    value = load_experiment_yaml(
      File.join(
        ROOT,
        "recipes/experiments/drone-fleet-performance/multi-host-scaling.yaml"
      )
    )
    value.dig("deployment", "allocation", "host_order").delete("cli-01")

    assert validate(value).any? { |error| error.include?("every deployment host") }
  end
end
