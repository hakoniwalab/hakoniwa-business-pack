# frozen_string_literal: true

require "set"

# Validates the Drone Fleet performance experiment family contract.
module ExperimentValidation
  module_function

  def non_empty_string?(value)
    value.is_a?(String) && !value.empty?
  end

  def integer_at_least?(value, minimum)
    value.is_a?(Integer) && value >= minimum
  end

  def boolean?(value)
    value == true || value == false
  end

  def validate_fields(errors, value, contract, prefix)
    unless value.is_a?(Hash)
      errors << "#{prefix} must be a mapping"
      return false
    end

    required = Array(contract["required_fields"])
    optional = Array(contract["optional_fields"])
    required.each do |field|
      errors << "#{prefix} missing #{field}" unless value.key?(field)
    end
    unless contract["extension_fields"] == true
      unknown = value.keys - required - optional
      errors << "#{prefix} has unknown fields: #{unknown.sort.join(', ')}" unless unknown.empty?
    end
    true
  end

  def validate(data, schema, label: "experiment")
    return ["#{label}: document must be a mapping"] unless data.is_a?(Hash)

    errors = []
    document = schema.fetch("document")
    validate_fields(errors, data, document, label)
    version_field = document.fetch("version_field")
    unless Array(document["supported_versions"]).include?(data[version_field])
      errors << "#{label}: unsupported #{version_field} #{data[version_field].inspect}"
    end

    sections = schema.fetch("sections")
    sections.each do |name, contract|
      next unless data.key?(name)

      validate_fields(errors, data[name], contract, "#{label}.#{name}")
    end

    validate_common_values(errors, data, schema, label)
    validate_conductor(errors, data.dig("runtime", "conductor"), schema, label) if data.dig("runtime", "conductor")
    validate_deployment(errors, data, schema, label) if data.key?("deployment")
    validate_visualization(errors, data, schema, label) if data.key?("visualization")
    errors
  end

  def validate_common_values(errors, data, schema, label)
    experiment = data["experiment"]
    if experiment.is_a?(Hash) && !non_empty_string?(experiment["id"])
      errors << "#{label}.experiment.id must be a non-empty string"
    end

    scale = data["scale"]
    if scale.is_a?(Hash)
      errors << "#{label}.scale.drone_count must be a positive integer" unless integer_at_least?(scale["drone_count"], 1)
      process_count = scale["process_count"]
      errors << "#{label}.scale.process_count must be a positive integer" unless integer_at_least?(process_count, 1)
      drones_per_process = scale["drones_per_process"]
      unless drones_per_process == "auto" || integer_at_least?(drones_per_process, 1)
        errors << "#{label}.scale.drones_per_process must be auto or a positive integer"
      end
    end

    runtime = data["runtime"]
    if runtime.is_a?(Hash)
      modes = Array(schema.dig("controlled_values", "runtime_modes"))
      errors << "#{label}.runtime.mode is invalid: #{runtime['mode'].inspect}" unless modes.include?(runtime["mode"])
      %w[visualization show_runner_real_time_sync].each do |field|
        errors << "#{label}.runtime.#{field} must be boolean" unless boolean?(runtime[field])
      end
    end

    scenario = data["scenario"]
    if scenario.is_a?(Hash) && !non_empty_string?(scenario["type"])
      errors << "#{label}.scenario.type must be a non-empty string"
    end

    results = data["results"]
    if results.is_a?(Hash)
      errors << "#{label}.results.enabled must be boolean" unless boolean?(results["enabled"])
      errors << "#{label}.results.directory must be a non-empty string" unless non_empty_string?(results["directory"])
    end

    measurement = data["measurement"]
    if measurement.is_a?(Hash) && !boolean?(measurement["enabled"])
      errors << "#{label}.measurement.enabled must be boolean"
    end

    matrix = data["matrix"]
    validate_matrix(errors, matrix, label) if matrix.is_a?(Hash)
  end

  def validate_matrix(errors, matrix, label)
    if matrix.key?("workloads") && (matrix.key?("drone_count") || matrix.key?("process_count"))
      errors << "#{label}.matrix.workloads cannot be combined with top-level drone_count or process_count"
    end
    %w[drone_count process_count].each do |field|
      next unless matrix.key?(field)

      values = matrix[field]
      unless values.is_a?(Array) && !values.empty? && values.all? { |item| integer_at_least?(item, 1) }
        errors << "#{label}.matrix.#{field} must be a non-empty positive integer list"
      end
    end
    if matrix.key?("conductor_real_sleep_msec")
      values = matrix["conductor_real_sleep_msec"]
      unless values.is_a?(Array) && !values.empty? && values.all? { |item| integer_at_least?(item, 0) }
        errors << "#{label}.matrix.conductor_real_sleep_msec must be a non-empty non-negative integer list"
      end
    end
    validate_attempts(errors, matrix["attempts"], label) if matrix.key?("attempts")
    return unless matrix.key?("workloads")

    workloads = matrix["workloads"]
    unless workloads.is_a?(Hash) && !workloads.empty?
      errors << "#{label}.matrix.workloads must be a non-empty mapping"
      return
    end
    seen_drone_counts = []
    workloads.each do |name, workload|
      prefix = "#{label}.matrix.workloads.#{name}"
      unless workload.is_a?(Hash)
        errors << "#{prefix} must be a mapping"
        next
      end
      unknown = workload.keys - %w[drone_count process_count]
      errors << "#{prefix} has unknown fields: #{unknown.join(', ')}" unless unknown.empty?
      drone_count = workload["drone_count"]
      errors << "#{prefix}.drone_count must be a positive integer" unless integer_at_least?(drone_count, 1)
      process_counts = workload["process_count"]
      unless process_counts.is_a?(Array) && !process_counts.empty? && process_counts.all? { |item| integer_at_least?(item, 1) }
        errors << "#{prefix}.process_count must be a non-empty positive integer list"
      else
        errors << "#{prefix}.process_count must not contain duplicates" unless process_counts.uniq.length == process_counts.length
        errors << "#{prefix}.process_count must be in ascending order" unless process_counts == process_counts.sort
      end
      seen_drone_counts << drone_count if integer_at_least?(drone_count, 1)
    end
    if seen_drone_counts.uniq.length != seen_drone_counts.length
      errors << "#{label}.matrix.workloads must not repeat drone_count"
    end
  end

  def validate_attempts(errors, attempts, label)
    prefix = "#{label}.matrix.attempts"
    return if integer_at_least?(attempts, 1)

    unless attempts.is_a?(Hash)
      errors << "#{prefix} must be a positive integer or an attempt policy"
      return
    end
    policy_fields = %w[baseline extension]
    unknown = attempts.keys - policy_fields
    errors << "#{prefix} has unknown fields: #{unknown.sort.join(', ')}" unless unknown.empty?
    policy_fields.each do |field|
      errors << "#{prefix} missing #{field}" unless attempts.key?(field)
    end

    baseline = attempts["baseline"]
    unless consecutive_attempts?(baseline, 1)
      errors << "#{prefix}.baseline must be a consecutive positive integer list starting at 1"
    end
    extension = attempts["extension"]
    unless extension.is_a?(Hash)
      errors << "#{prefix}.extension must be a mapping"
      return
    end
    extension_unknown = extension.keys - %w[attempts triggers]
    unless extension_unknown.empty?
      errors << "#{prefix}.extension has unknown fields: #{extension_unknown.sort.join(', ')}"
    end
    %w[attempts triggers].each do |field|
      errors << "#{prefix}.extension missing #{field}" unless extension.key?(field)
    end
    extension_attempts = extension["attempts"]
    expected_start = consecutive_attempts?(baseline, 1) ? baseline.last + 1 : nil
    unless expected_start && consecutive_attempts?(extension_attempts, expected_start)
      errors << "#{prefix}.extension.attempts must continue immediately after baseline"
    end
    validate_attempt_triggers(errors, extension["triggers"], prefix)
  end

  def consecutive_attempts?(values, first)
    values.is_a?(Array) && !values.empty? &&
      values.all? { |value| integer_at_least?(value, 1) } &&
      values == (first...(first + values.length)).to_a
  end

  def validate_attempt_triggers(errors, triggers, prefix)
    trigger_prefix = "#{prefix}.extension.triggers"
    unless triggers.is_a?(Hash)
      errors << "#{trigger_prefix} must be a mapping"
      return
    end
    unknown = triggers.keys - %w[any_failure relative_spread]
    errors << "#{trigger_prefix} has unknown fields: #{unknown.sort.join(', ')}" unless unknown.empty?
    %w[any_failure relative_spread].each do |field|
      errors << "#{trigger_prefix} missing #{field}" unless triggers.key?(field)
    end
    unless boolean?(triggers["any_failure"])
      errors << "#{trigger_prefix}.any_failure must be boolean"
    end
    spread = triggers["relative_spread"]
    unless spread.is_a?(Hash)
      errors << "#{trigger_prefix}.relative_spread must be a mapping"
      return
    end
    spread_unknown = spread.keys - %w[metric greater_than]
    unless spread_unknown.empty?
      errors << "#{trigger_prefix}.relative_spread has unknown fields: #{spread_unknown.sort.join(', ')}"
    end
    %w[metric greater_than].each do |field|
      errors << "#{trigger_prefix}.relative_spread missing #{field}" unless spread.key?(field)
    end
    unless spread["metric"] == "rtf"
      errors << "#{trigger_prefix}.relative_spread.metric must be rtf"
    end
    threshold = spread["greater_than"]
    unless threshold.is_a?(Numeric) && threshold.positive?
      errors << "#{trigger_prefix}.relative_spread.greater_than must be positive"
    end
  end

  def validate_deployment(errors, data, schema, label)
    deployment = data["deployment"]
    contract = schema.fetch("deployment")
    return unless validate_fields(errors, deployment, contract, "#{label}.deployment")

    mode = deployment["mode"]
    modes = Array(schema.dig("controlled_values", "deployment_modes"))
    errors << "#{label}.deployment.mode is invalid: #{mode.inspect}" unless modes.include?(mode)
    return unless mode == "multi_host"

    Array(contract["multi_host_required_fields"]).each do |field|
      errors << "#{label}.deployment missing #{field} for multi_host" unless deployment.key?(field)
    end
    validate_transport(errors, deployment["transport"], schema, label)
    validate_allocation(errors, deployment["allocation"], deployment["hosts"], schema, label) if deployment.key?("allocation")
    validate_hosts(errors, data, deployment, schema, label)
  end

  def validate_allocation(errors, allocation, hosts, schema, label)
    prefix = "#{label}.deployment.allocation"
    contract = schema.dig("deployment", "allocation")
    return unless validate_fields(errors, allocation, contract, prefix)

    modes = Array(contract["modes"])
    errors << "#{prefix}.mode is invalid: #{allocation['mode'].inspect}" unless modes.include?(allocation["mode"])
    order = allocation["host_order"]
    unless order.is_a?(Array) && !order.empty? && order.all? { |host_id| non_empty_string?(host_id) } && order.uniq.length == order.length
      errors << "#{prefix}.host_order must be a unique non-empty string list"
      return
    end
    if hosts.is_a?(Hash) && order.sort != hosts.keys.sort
      errors << "#{prefix}.host_order must contain every deployment host exactly once"
    end
  end

  def validate_transport(errors, transport, schema, label)
    prefix = "#{label}.deployment.transport"
    contract = schema.dig("deployment", "transport")
    return unless validate_fields(errors, transport, contract, prefix)

    transports = Array(schema.dig("controlled_values", "transports"))
    initiators = Array(schema.dig("controlled_values", "connection_initiators"))
    errors << "#{prefix}.type is invalid: #{transport['type'].inspect}" unless transports.include?(transport["type"])
    unless initiators.include?(transport["connection_initiator"])
      errors << "#{prefix}.connection_initiator is invalid: #{transport['connection_initiator'].inspect}"
    end
    unless integer_at_least?(transport["base_port"], 1) && transport["base_port"] <= 65_535
      errors << "#{prefix}.base_port must be an integer from 1 through 65535"
    end
  end

  def validate_hosts(errors, data, deployment, schema, label)
    hosts = deployment["hosts"]
    prefix = "#{label}.deployment.hosts"
    unless hosts.is_a?(Hash) && hosts.length >= 2
      errors << "#{prefix} must contain at least two named hosts"
      return
    end

    host_contract = schema.dig("deployment", "host")
    roles = Array(schema.dig("controlled_values", "host_roles"))
    hosts.each do |host_id, host|
      host_prefix = "#{prefix}.#{host_id}"
      errors << "#{prefix} has an invalid host id #{host_id.inspect}" unless non_empty_string?(host_id)
      next unless validate_fields(errors, host, host_contract, host_prefix)

      role = host["role"]
      errors << "#{host_prefix}.role is invalid: #{role.inspect}" unless roles.include?(role)
      platforms = Array(schema.dig("controlled_values", "host_platforms"))
      environments = Array(schema.dig("controlled_values", "execution_environments"))
      errors << "#{host_prefix}.platform is invalid: #{host['platform'].inspect}" unless platforms.include?(host["platform"])
      unless environments.include?(host["execution_environment"])
        errors << "#{host_prefix}.execution_environment is invalid: #{host['execution_environment'].inspect}"
      end
      errors << "#{host_prefix}.node_id must be a non-empty string" unless non_empty_string?(host["node_id"])
      drone_count = host["drone_count"]
      unless drone_count == "auto" || integer_at_least?(drone_count, 1)
        errors << "#{host_prefix}.drone_count must be auto or a positive integer"
      end
      errors << "#{host_prefix}.process_count must be a positive integer" unless integer_at_least?(host["process_count"], 1)
      start = host["global_start_index"]
      unless start == "auto" || integer_at_least?(start, 0)
        errors << "#{host_prefix}.global_start_index must be auto or a non-negative integer"
      end
      validate_role_fields(errors, host, role, host_contract, host_prefix)
    end

    servers = hosts.select { |_host_id, host| host.is_a?(Hash) && host["role"] == "server" }
    errors << "#{prefix} must contain exactly one server" unless servers.length == 1
    server_host = deployment["server_host"]
    unless non_empty_string?(server_host) && hosts.key?(server_host)
      errors << "#{label}.deployment.server_host must name a declared host"
    end
    if servers.length == 1 && server_host != servers.keys.first
      errors << "#{label}.deployment.server_host must name the host whose role is server"
    end

    hosts.each do |host_id, host|
      next unless host.is_a?(Hash) && host["role"] == "client"
      connect_to = host["connect_to"]
      unless connect_to == server_host
        errors << "#{prefix}.#{host_id}.connect_to must name deployment.server_host"
      end
    end

    validate_partition(errors, data["scale"], hosts, label)
  end

  def validate_role_fields(errors, host, role, contract, prefix)
    required = Array(contract["#{role}_required_fields"])
    forbidden = Array(contract["#{role}_forbidden_fields"])
    required.each do |field|
      unless host.key?(field) && non_empty_string?(host[field])
        errors << "#{prefix}.#{field} must be a non-empty string for role #{role}"
      end
    end
    forbidden.each do |field|
      errors << "#{prefix}.#{field} is forbidden for role #{role}" if host.key?(field)
    end
    expected_launcher_mode = "activate-only"
    unless host["launcher_mode"] == expected_launcher_mode
      errors << "#{prefix}.launcher_mode must be #{expected_launcher_mode} for role #{role}"
    end
  end

  def validate_conductor(errors, conductor, schema, label)
    prefix = "#{label}.runtime.conductor"
    contract = schema.fetch("conductor")
    return unless validate_fields(errors, conductor, contract, prefix)

    profile_id = conductor["profile"]
    profile = schema.dig("controlled_profiles", "conductor", profile_id)
    unless profile.is_a?(Hash)
      errors << "#{prefix}.profile is unknown: #{profile_id.inspect}"
      return
    end
    profile.each do |field, expected|
      next if conductor[field] == expected

      errors << "#{prefix}.#{field} must be #{expected.inspect} for profile #{profile_id}"
    end
    %w[delta_time_usec max_delay_time_usec].each do |field|
      errors << "#{prefix}.#{field} must be a positive integer" unless integer_at_least?(conductor[field], 1)
    end
    sleep_value = conductor["real_sleep_msec"]
    unless sleep_value == "unspecified" || integer_at_least?(sleep_value, 0)
      errors << "#{prefix}.real_sleep_msec must be unspecified or a non-negative integer"
    end
  end

  def validate_partition(errors, scale, hosts, label)
    return unless scale.is_a?(Hash)
    return unless hosts.values.all? { |host| host.is_a?(Hash) }

    drone_counts = hosts.values.map { |host| host["drone_count"] }
    process_counts = hosts.values.map { |host| host["process_count"] }
    starts = hosts.map { |host_id, host| [host["global_start_index"], host["drone_count"], host_id] }
    auto_allocation = drone_counts.any? { |count| count == "auto" }
    return if auto_allocation
    if drone_counts.all? { |count| integer_at_least?(count, 1) } && scale["drone_count"].is_a?(Integer)
      total = drone_counts.sum
      errors << "#{label}.deployment host drone_count total #{total} does not match scale.drone_count #{scale['drone_count']}" unless total == scale["drone_count"]
    end
    if process_counts.all? { |count| integer_at_least?(count, 1) } && scale["process_count"].is_a?(Integer)
      total = process_counts.sum
      errors << "#{label}.deployment host process_count total #{total} does not match scale.process_count #{scale['process_count']}" unless total == scale["process_count"]
    end
    return unless starts.all? { |start, count, _host_id| integer_at_least?(start, 0) && integer_at_least?(count, 1) }

    expected_start = 0
    starts.sort_by(&:first).each do |start, count, host_id|
      unless start == expected_start
        errors << "#{label}.deployment host drone ranges must be contiguous: #{host_id} starts at #{start}, expected #{expected_start}"
      end
      expected_start = start + count
    end
  end

  def validate_visualization(errors, data, schema, label)
    visualization = data["visualization"]
    contract = schema.fetch("visualization")
    prefix = "#{label}.visualization"
    return unless validate_fields(errors, visualization, contract, prefix)

    deployment = data["deployment"]
    hosts = deployment.is_a?(Hash) ? deployment["hosts"] : nil
    unless deployment.is_a?(Hash) && deployment["mode"] == "multi_host" && hosts.is_a?(Hash)
      errors << "#{prefix} is only valid with a multi_host deployment"
      return
    end
    unless data.dig("runtime", "visualization") == true
      errors << "#{prefix} requires runtime.visualization true"
    end
    %w[bridge_host viewer_host].each do |field|
      errors << "#{prefix}.#{field} must name a declared host" unless hosts.key?(visualization[field])
    end
    unless integer_at_least?(visualization["max_drones_per_packet"], 1)
      errors << "#{prefix}.max_drones_per_packet must be a positive integer"
    end

    publishers = visualization["publishers"]
    unless publishers.is_a?(Hash) && !publishers.empty?
      errors << "#{prefix}.publishers must be a non-empty host mapping"
      return
    end
    missing = hosts.keys - publishers.keys
    extra = publishers.keys - hosts.keys
    errors << "#{prefix}.publishers missing hosts: #{missing.sort.join(', ')}" unless missing.empty?
    errors << "#{prefix}.publishers has unknown hosts: #{extra.sort.join(', ')}" unless extra.empty?

    chunks = []
    publishers.each do |host_id, publisher|
      publisher_prefix = "#{prefix}.publishers.#{host_id}"
      next unless validate_fields(errors, publisher, contract.fetch("publisher"), publisher_prefix)

      chunk = publisher["chunk_index"]
      errors << "#{publisher_prefix}.chunk_index must be a non-negative integer" unless integer_at_least?(chunk, 0)
      chunks << chunk if chunk.is_a?(Integer)
      expected_pdu = chunk.is_a?(Integer) ? "drone_visual_state_array_#{chunk}" : nil
      unless publisher["pdu_name"] == expected_pdu
        errors << "#{publisher_prefix}.pdu_name must match its chunk_index"
      end
      unless publisher["transfer_policy"] == "immediate-atomic"
        errors << "#{publisher_prefix}.transfer_policy must be immediate-atomic"
      end
    end
    errors << "#{prefix}.publisher chunk_index values must be unique" unless chunks.uniq.length == chunks.length
    subscriptions = visualization["bridge_subscriptions"]
    unless subscriptions.is_a?(Array) && subscriptions.all? { |chunk| integer_at_least?(chunk, 0) }
      errors << "#{prefix}.bridge_subscriptions must be a non-negative integer list"
    else
      errors << "#{prefix}.bridge_subscriptions must match publisher chunks" unless subscriptions.sort == chunks.sort
    end
  end
end
