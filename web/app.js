const state = {
  controllers: [],
  sources: [],
  sensors: [],
  sensorErrors: {},
  powerSwitches: [],
  curves: [],
  manualFanSelections: {},
  editingSwitchId: null,
  switchSensorGroups: [],
  curveSensorGroups: [],
  collapsedCurveSensors: {},
  curveSensorSelections: {},
  curveTargetControllers: [],
  collapsedCurveTargets: {},
  curveTargetSelections: {},
  curvePoints: [
    { temp: 30, pwm: 25 },
    { temp: 50, pwm: 45 },
    { temp: 70, pwm: 75 },
    { temp: 85, pwm: 100 },
  ],
};

const qs = (selector) => document.querySelector(selector);
const qsa = (root, selector) => [...root.querySelectorAll(selector)];
const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  children.forEach((child) => node.append(child));
  return node;
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.error || payload.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

async function loadStatus() {
  const payload = await api("/api/status");
  state.controllers = payload.controllers || [];
  state.sources = payload.sources || [];
  state.sensors = payload.sensors || [];
  state.sensorErrors = payload.sensor_errors || {};
  state.powerSwitches = payload.power_switches || [];
  state.curves = payload.curves || [];
  render();
}

function render() {
  qs("#controller-count").textContent = state.controllers.length;
  qs("#sensor-count").textContent = state.sensors.length;
  qs("#switch-count").textContent = state.powerSwitches.length;
  qs("#curve-count").textContent = state.curves.length;
  qs("#summary").textContent = `${state.controllers.length} controller(s), ${state.sensors.length} sensor(s), ${state.powerSwitches.length} switch(es), ${state.curves.length} curve(s)`;
  updateCurveFormMode();
  if (!hasFocusedTextInput("#controllers")) {
    renderControllers();
  }
  if (!hasFocusedControl("#manual-form")) {
    renderManualOptions();
    renderManualFans();
  }
  renderSources();
  renderSensors();
  if (!hasFocusedControl("#switch-form")) {
    renderSwitchSensorOptions();
  }
  renderPowerSwitches();
  if (!hasFocusedControl("#curve-form")) {
    renderCurveSensorOptions();
    renderCurveTargets();
    renderCurvePoints();
  }
  renderCurves();
}

function updateCurveFormMode() {
  const button = qs("#curve-form button[type='submit']");
  button.textContent = "Save Curve";
  const isFlat = qs("#curve-type").value === "flat";
  const sensorField = qs("#curve-sensor-field");
  const tuningField = qs("#curve-tuning-field");
  if (sensorField) sensorField.hidden = isFlat;
  if (tuningField) tuningField.hidden = isFlat;
  qs("#add-curve-point").hidden = isFlat;
  if (isFlat && !state.curvePoints.length) {
    state.curvePoints = [{ temp: 0, pwm: 45 }];
  }
  if (isFlat && state.curvePoints.length > 1) {
    state.curvePoints = [state.curvePoints[0]];
  }
}

function hasFocusedControl(selector) {
  const root = qs(selector);
  return !!root && root.contains(document.activeElement);
}

function hasFocusedTextInput(selector) {
  const active = document.activeElement;
  return hasFocusedControl(selector) && active && active.matches("input[type='text'], input[type='url'], input[type='number'], textarea");
}

function controllerLabel(controller) {
  return controller.display_name || controller.name || controller.serial_number;
}

function controllerById(identifier) {
  return state.controllers.find((controller) => controller.identifier === identifier);
}

function fanList(controller) {
  return (controller?.fans || []).map((fan) => ({
    id: String(fan.id),
    label: fan.label || `Fan ${fan.id}`,
    rpm: fan.rpm,
  }));
}

function sensorSourceName(sensor) {
  if (sensor.source) return sensor.source;
  const id = String(sensor.id || "");
  return id.includes("-") ? id.split("-")[0] : "Sensors";
}

function sensorDisplayName(sensor) {
  return sensor.label || sensor.name || sensor.id;
}

function groupedSensors() {
  const groups = new Map();
  state.sensors.forEach((sensor) => {
    const source = sensorSourceName(sensor);
    const current = groups.get(source) || [];
    current.push(sensor);
    groups.set(source, current);
  });
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
}

function configuredSensorGroups() {
  return state.sources.map((source) => source.name).sort((left, right) => left.localeCompare(right));
}

function sensorsForSource(source) {
  return state.sensors
    .filter((sensor) => sensorSourceName(sensor) === source)
    .sort((left, right) => sensorDisplayName(left).localeCompare(sensorDisplayName(right)));
}

function sensorGroupNamesForIds(sensorIds) {
  const groups = new Set();
  sensorIds.forEach((sensorId) => {
    const sensor = state.sensors.find((item) => item.id === sensorId);
    if (sensor) {
      groups.add(sensorSourceName(sensor));
      return;
    }
    const fallback = String(sensorId || "").split("-")[0];
    if (fallback) groups.add(fallback);
  });
  return [...groups].sort((left, right) => left.localeCompare(right));
}

function currentCurveNameKey() {
  return qs("#curve-name").value.trim().toLowerCase();
}

function assignedFansByControllerForOtherCurves() {
  const currentName = currentCurveNameKey();
  const assigned = new Map();

  state.curves.forEach((curve) => {
    if (curve.name.toLowerCase() === currentName) return;
    curve.targets.forEach((target) => {
      const entry = assigned.get(target.controller_id) || { all: false, fans: new Set() };
      if (target.fan === "all") {
        entry.all = true;
      } else {
        entry.fans.add(String(target.fan));
      }
      assigned.set(target.controller_id, entry);
    });
  });

  return assigned;
}

function availableFansForController(controller) {
  const fans = fanList(controller);
  const assigned = assignedFansByControllerForOtherCurves().get(controller.identifier);
  if (!assigned) return fans;
  if (assigned.all) return [];
  return fans.filter((fan) => !assigned.fans.has(fan.id));
}

function renderControllers() {
  const root = qs("#controllers");
  root.replaceChildren();
  if (!state.controllers.length) {
    root.append(el("div", { className: "empty", text: "Auto-detect is polling for fan controllers." }));
    return;
  }

  state.controllers.forEach((controller) => {
    const input = el("input", {
      type: "text",
      value: controller.name || "",
      placeholder: controller.serial_number,
      autocomplete: "off",
    });
    const save = el("button", { type: "button", text: "Save" });
    save.addEventListener("click", async () => {
      await api(`/api/controllers/${encodeURIComponent(controller.identifier)}/name`, {
        method: "POST",
        body: JSON.stringify({ name: input.value }),
      });
      save.blur();
      await loadStatus();
    });

    root.append(
      el("div", { className: "item" }, [
        el("div", { className: "item-main" }, [
          el("div", {}, [
            el("div", { className: "item-title", text: controllerLabel(controller) }),
            el("div", { className: "item-meta", text: controller.serial_number }),
            el("div", { className: "item-meta", text: `${controller.device} - ${controller.description}` }),
          ]),
          el("span", { className: controller.connected ? "pill ok" : "pill warn", text: controller.connected ? "online" : "offline" }),
        ]),
        el("div", { className: "inline-edit" }, [input, save]),
      ])
    );
  });
}

function renderManualOptions() {
  const select = qs("#manual-controller");
  const current = select.value;
  select.replaceChildren();
  state.controllers.forEach((controller) => {
    select.append(el("option", { value: controller.identifier, text: controllerLabel(controller) }));
  });
  if (state.controllers.some((controller) => controller.identifier === current)) {
    select.value = current;
  }
}

function renderManualFans() {
  const root = qs("#manual-fans");
  syncManualFanSelectionsFromDom();
  const controller = controllerById(qs("#manual-controller").value);
  root.replaceChildren();
  if (!controller) {
    root.append(el("div", { className: "empty", text: "No controller selected." }));
    return;
  }

  const fans = fanList(controller);
  if (!fans.length) {
    root.append(el("div", { className: "empty", text: "No fan RPM data available yet." }));
    return;
  }

  const selected = new Set(
    (state.manualFanSelections[controller.identifier] || []).map((fan) => `${controller.identifier}:${fan}`)
  );
  root.append(buildFanCheckboxGroup("manual", controller.identifier, fans, selected));
}

function syncManualFanSelectionsFromDom() {
  const root = qs("#manual-fans");
  if (!root) return;
  const checked = qsa(root, "input[data-controller][data-fan]:checked");
  if (!checked.length && !qsa(root, "input[data-controller][data-fan]").length) return;
  const controllerId = checked[0]?.dataset.controller || qsa(root, "input[data-controller][data-fan]")[0]?.dataset.controller;
  if (!controllerId) return;
  state.manualFanSelections[controllerId] = checked.map((input) => input.dataset.fan);
}

function renderSources() {
  const root = qs("#sources");
  root.replaceChildren();
  if (!state.sources.length) {
    root.append(el("div", { className: "empty", text: "No sensor URLs configured." }));
    return;
  }

  state.sources.forEach((source) => {
    const remove = el("button", { className: "danger", type: "button", text: "Remove" });
    remove.addEventListener("click", async () => {
      await api(`/api/sources/${encodeURIComponent(source.name)}`, { method: "DELETE" });
      await loadStatus();
    });
    root.append(
      el("div", { className: "item" }, [
        el("div", { className: "item-main" }, [
          el("div", {}, [
            el("div", { className: "item-title", text: source.name }),
            el("div", { className: "item-meta", text: source.url }),
          ]),
          remove,
        ]),
      ])
    );
  });
}

function renderSensors() {
  const root = qs("#sensors");
  root.replaceChildren();
  if (!state.sensors.length) {
    root.append(el("div", { className: "empty", text: "Temperature sensors will appear after a URL is added." }));
  } else {
    groupedSensors().forEach(([source, sensors]) => {
      const highest = sensors.reduce((winner, sensor) => {
        if (!winner) return sensor;
        return Number(sensor.value) > Number(winner.value) ? sensor : winner;
      }, null);
      root.append(
        el("div", { className: "sensor sensor-group" }, [
          el("strong", { text: source }),
          el("div", { className: "sensor-value", text: highest ? formatTemperature(highest.value) : "waiting" }),
          el("div", {
            className: "item-meta",
            text: highest
              ? `Highest: ${sensorDisplayName(highest)}`
              : "No temperature values available.",
          }),
        ])
      );
    });
  }

  const errors = Object.entries(state.sensorErrors);
  qs("#sensor-errors").replaceChildren(
    ...errors.map(([name, message]) => el("div", { text: `${name}: ${message}` }))
  );
}

function formatTemperature(value) {
  return `${Number(value).toFixed(1)}\u00b0C`;
}

function formatSeconds(value) {
  return `${Number(value).toFixed(1)}s`;
}

function setCurveFormStatus(message, kind = "warn") {
  const status = qs("#curve-form-status");
  status.textContent = message;
  status.className = `form-status ${kind}`;
}

function setSwitchFormStatus(message, kind = "warn") {
  const status = qs("#switch-form-status");
  status.textContent = message;
  status.className = `form-status ${kind}`;
}

function switchGroupsAssignedToOtherSwitches() {
  const assigned = new Set();
  state.powerSwitches.forEach((powerSwitch) => {
    if (powerSwitch.id === state.editingSwitchId) return;
    (powerSwitch.sensor_groups || []).forEach((group) => assigned.add(group));
  });
  return assigned;
}

function renderSwitchSensorOptions() {
  const select = qs("#switch-sensor-add");
  const current = select.value;
  const assigned = switchGroupsAssignedToOtherSwitches();
  const available = configuredSensorGroups().filter(
    (group) => !assigned.has(group) && !state.switchSensorGroups.includes(group)
  );
  select.replaceChildren();
  if (!available.length) {
    select.append(el("option", { value: "", text: configuredSensorGroups().length ? "No available sensor groups" : "No sensor groups configured" }));
    select.disabled = true;
    qs("#add-switch-sensor").disabled = true;
  } else {
    select.disabled = false;
    qs("#add-switch-sensor").disabled = false;
    available.forEach((group) => {
      select.append(el("option", { value: group, text: group }));
    });
    if (available.includes(current)) select.value = current;
  }
  renderSwitchSelectedGroups();
}

function renderSwitchSelectedGroups() {
  const root = qs("#switch-sensors");
  root.replaceChildren();
  if (!state.switchSensorGroups.length) {
    root.append(el("div", { className: "empty compact", text: "No sensor groups assigned. This switch will be kept off." }));
    return;
  }

  state.switchSensorGroups.forEach((group) => {
    const remove = el("button", { type: "button", className: "danger compact-button", text: "Remove" });
    remove.addEventListener("click", () => {
      state.switchSensorGroups = state.switchSensorGroups.filter((item) => item !== group);
      renderSwitchSensorOptions();
    });
    root.append(
      el("div", { className: "item compact-item" }, [
        el("div", { className: "item-main" }, [
          el("div", { className: "item-title", text: group }),
          remove,
        ]),
      ])
    );
  });
}

function renderPowerSwitches() {
  const root = qs("#switches");
  root.replaceChildren();
  if (!state.powerSwitches.length) {
    root.append(el("div", { className: "empty", text: "No power switches configured." }));
    return;
  }

  state.powerSwitches.forEach((powerSwitch) => {
    const edit = el("button", { className: "secondary", type: "button", text: "Edit" });
    edit.addEventListener("click", () => loadSwitchIntoEditor(powerSwitch));
    const remove = el("button", { className: "danger", type: "button", text: "Remove" });
    remove.addEventListener("click", async () => {
      await api(`/api/power-switches/${encodeURIComponent(powerSwitch.id)}`, { method: "DELETE" });
      if (state.editingSwitchId === powerSwitch.id) clearSwitchEditor();
      await loadStatus();
    });

    root.append(
      el("div", { className: "item" }, [
        el("div", { className: "item-main" }, [
          el("div", {}, [
            el("div", { className: "item-title", text: powerSwitch.name }),
            el("div", { className: "item-meta", text: powerSwitch.url || switchBaseUrl(powerSwitch) }),
            el("div", { className: powerSwitch.state === "on" ? "item-meta ok" : powerSwitch.state === "off" ? "item-meta warn" : "item-meta", text: `State: ${powerSwitch.state || "unknown"}` }),
            el("div", { className: "item-meta", text: `Desired: ${powerSwitch.desired_state || "off"}` }),
            el("div", { className: "item-meta", text: `Last command: ${powerSwitch.last_command || "none"}` }),
            el("div", { className: "item-meta", text: `Sensor Groups: ${(powerSwitch.sensor_groups || []).join(", ") || "none"}` }),
            powerSwitch.last_error ? el("div", { className: "item-meta warn", text: powerSwitch.last_error }) : document.createDocumentFragment(),
          ]),
          el("div", { className: "curve-actions" }, [edit, remove]),
        ]),
      ])
    );
  });
}

function loadSwitchIntoEditor(powerSwitch) {
  state.editingSwitchId = powerSwitch.id;
  qs("#switch-name").value = powerSwitch.name;
  qs("#switch-url").value = powerSwitch.url || switchBaseUrl(powerSwitch);
  state.switchSensorGroups = [...(powerSwitch.sensor_groups || [])];
  renderSwitchSensorOptions();
  setSwitchFormStatus(`Loaded ${powerSwitch.name}. Saving with the same name will update it.`, "ok");
}

function switchBaseUrl(powerSwitch) {
  const url = powerSwitch.on_url || powerSwitch.off_url || powerSwitch.state_url || "";
  return url.replace(/\/(on|off|state)\/?$/i, "");
}

function clearSwitchEditor() {
  state.editingSwitchId = null;
  state.switchSensorGroups = [];
  qs("#switch-form").reset();
  renderSwitchSensorOptions();
  setSwitchFormStatus("");
}

function renderCurveSensorOptions() {
  const root = qs("#curve-sensors");
  syncCurveSensorSelectionsFromDom();
  root.replaceChildren();
  state.curveSensorGroups = state.curveSensorGroups.filter((source) => sensorsForSource(source).length > 0);
  renderCurveSensorAddOptions();

  if (!state.sensors.length) {
    root.append(el("div", { className: "empty", text: "No sensors available." }));
    return;
  }

  if (!state.curveSensorGroups.length) {
    root.append(el("div", { className: "empty", text: "Add a sensor group to this curve." }));
    return;
  }

  state.curveSensorGroups.forEach((source) => {
    const sensors = sensorsForSource(source);
    const collapsed = !!state.collapsedCurveSensors[source];
    const allSensorControl = buildAllSensorsCheckbox(source);
    const collapse = el("button", {
      type: "button",
      className: "secondary compact-button",
      text: collapsed ? "Expand" : "Collapse",
    });
    collapse.addEventListener("click", () => {
      syncCurveSensorSelectionsFromDom();
      state.collapsedCurveSensors[source] = !collapsed;
      renderCurveSensorOptions();
    });

    const remove = el("button", {
      type: "button",
      className: "danger compact-button",
      text: "Remove",
    });
    remove.addEventListener("click", () => {
      state.curveSensorGroups = state.curveSensorGroups.filter((item) => item !== source);
      delete state.collapsedCurveSensors[source];
      delete state.curveSensorSelections[source];
      renderCurveSensorOptions();
    });

    const group = el("div", { className: "sensor-option-group target-group" }, [
      el("div", { className: "target-header" }, [
        el("label", { className: "target-title sensor-all-sensors" }, [
          allSensorControl,
          document.createTextNode(source),
        ]),
        el("div", { className: "target-actions" }, [collapse, remove]),
      ]),
    ]);

    if (!collapsed) {
      if (!sensors.length) {
        group.append(el("div", { className: "empty compact", text: "No sensors available in this group." }));
      } else {
        group.append(buildSensorCheckboxGroup(source, sensors));
      }
    }

    root.append(group);
  });
}

function buildAllSensorsCheckbox(source) {
  const input = el("input", { type: "checkbox" });
  input.dataset.source = source;
  const selected = new Set(state.curveSensorSelections[source] || []);
  const sensors = sensorsForSource(source);
  input.checked = sensors.length > 0 && sensors.every((sensor) => selected.has(sensor.id));
  input.addEventListener("change", () => {
    const root = qs("#curve-sensors");
    qsa(root, `input[data-source="${CSS.escape(source)}"][data-sensor]`).forEach((item) => {
      item.checked = input.checked;
    });
    state.curveSensorSelections[source] = input.checked ? sensors.map((sensor) => sensor.id) : [];
  });
  return input;
}

function buildSensorCheckboxGroup(source, sensors) {
  const group = el("div", { className: "check-grid sensor-checks" });
  const selected = new Set(state.curveSensorSelections[source] || []);

  sensors.forEach((sensor) => {
    const input = el("input", { type: "checkbox", value: sensor.id });
    input.dataset.source = source;
    input.dataset.sensor = sensor.id;
    input.checked = selected.has(sensor.id);
    input.addEventListener("change", () => {
      const headerAll = qs(`.sensor-all-sensors input[data-source="${CSS.escape(source)}"]`);
      const sensorInputs = qsa(qs("#curve-sensors"), `input[data-source="${CSS.escape(source)}"][data-sensor]`);
      const allChecked = sensorInputs.length > 0 && sensorInputs.every((item) => item.checked);
      if (headerAll) headerAll.checked = allChecked;
      syncCurveSensorSelectionsFromDom();
    });
    const value = sensor.value == null ? "" : ` (${formatTemperature(sensor.value)})`;
    group.append(
      el("label", { className: "check-item" }, [
        input,
        document.createTextNode(`${sensorDisplayName(sensor)}${value}`),
      ])
    );
  });

  return group;
}

function renderCurveSensorAddOptions() {
  const select = qs("#curve-sensor-add");
  const current = select.value;
  select.replaceChildren();

  const available = groupedSensors().filter(([source]) => !state.curveSensorGroups.includes(source));
  if (!available.length) {
    select.append(el("option", { value: "", text: state.sensors.length ? "No available sensor groups" : "No sensors available" }));
    select.disabled = true;
    qs("#add-curve-sensor").disabled = true;
    return;
  }

  select.disabled = false;
  qs("#add-curve-sensor").disabled = false;
  available.forEach(([source]) => {
    select.append(el("option", { value: source, text: source }));
  });
  if (available.some(([source]) => source === current)) {
    select.value = current;
  }
}

function syncCurveSensorSelectionsFromDom() {
  const root = qs("#curve-sensors");
  if (!root) return;
  const visibleSources = new Set(qsa(root, "input[data-source][data-sensor]").map((input) => input.dataset.source));
  visibleSources.forEach((source) => {
    state.curveSensorSelections[source] = qsa(
      root,
      `input[data-source="${CSS.escape(source)}"][data-sensor]:checked`
    ).map((input) => input.dataset.sensor);
  });
}

function selectedCurveSensors() {
  syncCurveSensorSelectionsFromDom();
  return state.curveSensorGroups.flatMap((source) => state.curveSensorSelections[source] || []);
}

function renderCurveTargets() {
  const root = qs("#curve-targets");
  syncCurveTargetSelectionsFromDom();
  const previous = curveTargetSelectionSet();
  state.curveTargetControllers = state.curveTargetControllers.filter((identifier) => controllerById(identifier));
  renderCurveControllerAddOptions();
  root.replaceChildren();

  if (!state.controllers.length) {
    root.append(el("div", { className: "empty", text: "No controllers available." }));
    return;
  }

  if (!state.curveTargetControllers.length) {
    root.append(el("div", { className: "empty", text: "Add a controller to this curve to select its fans." }));
    return;
  }

  state.curveTargetControllers.forEach((identifier) => {
    const controller = controllerById(identifier);
    if (!controller) return;
    const fans = availableFansForController(controller);
    const collapsed = !!state.collapsedCurveTargets[controller.identifier];
    const allFanControl = buildAllFansCheckbox(controller.identifier);
    const collapse = el("button", {
      type: "button",
      className: "secondary compact-button",
      text: collapsed ? "Expand" : "Collapse",
    });
    collapse.addEventListener("click", () => {
      syncCurveTargetSelectionsFromDom();
      state.collapsedCurveTargets[controller.identifier] = !collapsed;
      renderCurveTargets();
    });

    const remove = el("button", {
      type: "button",
      className: "danger compact-button",
      text: "Remove",
    });
    remove.addEventListener("click", () => {
      state.curveTargetControllers = state.curveTargetControllers.filter((item) => item !== controller.identifier);
      delete state.collapsedCurveTargets[controller.identifier];
      delete state.curveTargetSelections[controller.identifier];
      renderCurveTargets();
    });

    const group = el("div", { className: "target-group" }, [
      el("div", { className: "target-header" }, [
        el("label", { className: "target-title target-all-fans" }, [
          allFanControl,
          document.createTextNode(controllerLabel(controller)),
        ]),
        el("div", { className: "target-actions" }, [collapse, remove]),
      ]),
    ]);

    if (!collapsed) {
      if (!fans.length) {
        group.append(el("div", { className: "empty compact", text: "No available fans on this controller." }));
      } else {
        group.append(buildFanCheckboxGroup("curve", controller.identifier, fans, previous));
      }
    }

    root.append(group);
  });
}

function buildAllFansCheckbox(controllerId) {
  const input = el("input", { type: "checkbox" });
  input.dataset.controller = controllerId;
  const selected = new Set(state.curveTargetSelections[controllerId] || []);
  const controller = controllerById(controllerId);
  const availableFans = controller ? availableFansForController(controller) : [];
  input.checked = availableFans.length > 0 && availableFans.every((fan) => selected.has(fan.id));
  input.addEventListener("change", () => {
    const root = qs("#curve-targets");
    qsa(root, `input[data-controller="${CSS.escape(controllerId)}"][data-fan]`).forEach((item) => {
      item.checked = input.checked;
    });
    syncCurveTargetSelectionsFromDom();
  });
  return input;
}

function renderCurveControllerAddOptions() {
  const select = qs("#curve-controller-add");
  const current = select.value;
  select.replaceChildren();

  const available = state.controllers.filter(
    (controller) => !state.curveTargetControllers.includes(controller.identifier) && availableFansForController(controller).length > 0
  );
  if (!available.length) {
    select.append(el("option", { value: "", text: state.controllers.length ? "No available fans" : "No controllers available" }));
    select.disabled = true;
    qs("#add-curve-controller").disabled = true;
    return;
  }

  select.disabled = false;
  qs("#add-curve-controller").disabled = false;
  available.forEach((controller) => {
    select.append(el("option", { value: controller.identifier, text: controllerLabel(controller) }));
  });
  if (available.some((controller) => controller.identifier === current)) {
    select.value = current;
  }
}

function buildFanCheckboxGroup(scope, controllerId, fans, previous = new Set()) {
  const group = el("div", { className: "check-grid fan-checks" });
  const allInput = el("input", { type: "checkbox" });
  allInput.dataset.controller = controllerId;
  allInput.dataset.fan = "all";

  const fanInputs = fans.map((fan) => {
    const input = el("input", { type: "checkbox" });
    input.dataset.controller = controllerId;
    input.dataset.fan = fan.id;
    input.checked = previous.has(`${controllerId}:all`) || previous.has(`${controllerId}:${fan.id}`);
    return { fan, input };
  });

  allInput.checked = previous.has(`${controllerId}:all`) || fanInputs.every(({ input }) => input.checked);
  allInput.addEventListener("change", () => {
    fanInputs.forEach(({ input }) => {
      input.checked = allInput.checked;
    });
    if (scope === "curve") syncCurveTargetSelectionsFromDom();
    if (scope === "manual") syncManualFanSelectionsFromDom();
  });

  if (scope === "manual") {
    group.append(el("label", { className: "check-item all-fans" }, [allInput, document.createTextNode("All fans")]));
  }

  fanInputs.forEach(({ fan, input }) => {
    input.addEventListener("change", () => {
      const headerAll = qs(`.target-all-fans input[data-controller="${CSS.escape(controllerId)}"]`);
      const allChecked = fanInputs.every((item) => item.input.checked);
      allInput.checked = allChecked;
      if (headerAll) headerAll.checked = allChecked;
      if (scope === "curve") syncCurveTargetSelectionsFromDom();
      if (scope === "manual") syncManualFanSelectionsFromDom();
    });
    const rpm = fan.rpm == null ? "" : ` (${fan.rpm} RPM)`;
    group.append(el("label", { className: "check-item" }, [input, document.createTextNode(`${fan.label}${rpm}`)]));
  });

  if (scope === "manual" && previous.size === 0 && !state.manualFanSelections[controllerId]) {
    allInput.checked = true;
    fanInputs.forEach(({ input }) => {
      input.checked = true;
    });
    state.manualFanSelections[controllerId] = ["all", ...fans.map((fan) => fan.id)];
  }

  return group;
}

function curveTargetSelectionSet() {
  const selected = new Set();
  Object.entries(state.curveTargetSelections).forEach(([controllerId, fans]) => {
    fans.forEach((fan) => selected.add(`${controllerId}:${fan}`));
  });
  return selected;
}

function syncCurveTargetSelectionsFromDom() {
  const root = qs("#curve-targets");
  if (!root) return;
  const visibleControllers = new Set(qsa(root, "input[data-controller][data-fan]").map((input) => input.dataset.controller));
  visibleControllers.forEach((controllerId) => {
    state.curveTargetSelections[controllerId] = qsa(
      root,
      `input[data-controller="${CSS.escape(controllerId)}"][data-fan]:checked`
    ).map((input) => input.dataset.fan);
  });
}

function renderCurvePoints(syncFromDom = true) {
  const root = qs("#curve-points-editor");
  if (syncFromDom) {
    syncCurvePointsFromDom();
  }
  const isFlat = qs("#curve-type").value === "flat";
  if (isFlat && !state.curvePoints.length) {
    state.curvePoints = [{ temp: 0, pwm: 45 }];
  }
  if (isFlat && state.curvePoints.length > 1) {
    state.curvePoints = [state.curvePoints[0]];
  }
  root.replaceChildren();

  state.curvePoints.forEach((point, index) => {
    const temp = el("input", { type: "number", min: "0", max: "120", step: "1", value: point.temp });
    const pwm = el("input", { type: "number", min: "0", max: "100", step: "1", value: point.pwm });
    temp.dataset.pointField = "temp";
    pwm.dataset.pointField = "pwm";
    const remove = el("button", { type: "button", className: "danger compact-button", text: "Remove" });
    remove.hidden = isFlat;
    remove.addEventListener("click", () => {
      syncCurvePointsFromDom();
      state.curvePoints.splice(index, 1);
      renderCurvePoints(false);
    });

    const children = isFlat
      ? [
          el("input", { type: "hidden", value: "0" }),
          el("label", {}, [document.createTextNode("PWM"), pwm]),
          remove,
        ]
      : [
          el("label", {}, [document.createTextNode("Temp"), temp]),
          el("label", {}, [document.createTextNode("PWM"), pwm]),
          remove,
        ];
    if (isFlat) {
      children[0].dataset.pointField = "temp";
    }

    root.append(
      el("div", { className: isFlat ? "point-row flat-point-row" : "point-row" }, children)
    );
  });
}

function syncCurvePointsFromDom() {
  const rows = qsa(qs("#curve-points-editor"), ".point-row");
  if (!rows.length) return;
  state.curvePoints = rows.map((row) => {
    const temp = Number(row.querySelector('[data-point-field="temp"]').value);
    const pwm = Number(row.querySelector('[data-point-field="pwm"]').value);
    return { temp, pwm };
  });
}

function renderCurves() {
  const root = qs("#curves");
  root.replaceChildren();
  if (!state.curves.length) {
    root.append(el("div", { className: "empty", text: "No curves configured." }));
    return;
  }

  state.curves.forEach((curve) => {
    const toggle = el("input", { type: "checkbox" });
    toggle.checked = curve.enabled;
    toggle.addEventListener("change", async () => {
      await api(`/api/curves/${encodeURIComponent(curve.id)}/enabled`, {
        method: "POST",
        body: JSON.stringify({ enabled: toggle.checked }),
      });
      await loadStatus();
    });

    const remove = el("button", { className: "danger", type: "button", text: "Remove" });
    remove.addEventListener("click", async () => {
      await api(`/api/curves/${encodeURIComponent(curve.id)}`, { method: "DELETE" });
      await loadStatus();
    });

    const edit = el("button", { className: "secondary", type: "button", text: "Edit" });
    edit.addEventListener("click", () => {
      loadCurveIntoEditor(curve);
    });

    const value = curve.curve_type === "flat" && curve.last_pwm != null
      ? `${Number(curve.last_pwm).toFixed(0)}%`
      : curve.last_value == null
        ? "waiting"
        : `${formatTemperature(curve.last_value)} -> ${Number(curve.last_pwm).toFixed(0)}%`;
    const rpmRows = curveTargetRpmRows(curve.targets);
    const status = curve.last_error || value;

    root.append(
      el("div", { className: "item" }, [
        el("div", { className: "item-main" }, [
          el("div", {}, [
            el("div", { className: "item-title", text: curve.name }),
            el("div", { className: "item-meta", text: `Type: ${curve.curve_type || "linear"}` }),
            el("div", { className: "item-meta", text: `Sensor Groups: ${sensorGroupNamesForIds(curve.sensor_ids || []).join(", ") || "none"}` }),
            curve.curve_type === "flat"
              ? document.createDocumentFragment()
              : el("div", {
                  className: "item-meta",
                  text: `Hysteresis: ${formatTemperature(curve.hysteresis ?? 2)} / Response: ${formatSeconds(curve.response_time ?? 1)}`,
                }),
            el("div", { className: "rpm-list" }, rpmRows),
            el("div", { className: curve.last_error ? "item-meta warn" : "item-meta ok", text: status }),
          ]),
          el("label", { className: "check-item" }, [toggle, document.createTextNode("Enabled")]),
        ]),
        el("div", { className: "curve-actions" }, [edit, remove]),
      ])
    );
  });
}

function loadCurveIntoEditor(curve) {
  qs("#curve-name").value = curve.name;
  qs("#curve-type").value = curve.curve_type || "linear";
  qs("#curve-hysteresis").value = curve.hysteresis ?? 2;
  qs("#curve-response-time").value = curve.response_time ?? 1;
  state.curvePoints = (curve.points || []).map((point) => ({ temp: point.temp, pwm: point.pwm }));
  if ((curve.curve_type || "linear") === "flat") {
    state.curvePoints = [state.curvePoints[0] || { temp: 0, pwm: 45 }];
  }
  state.curveSensorGroups = [
    ...new Set(
      (curve.sensor_ids || [])
        .map((sensorId) => state.sensors.find((sensor) => sensor.id === sensorId))
        .filter(Boolean)
        .map(sensorSourceName)
    ),
  ];
  state.collapsedCurveSensors = {};
  state.curveSensorSelections = {};
  state.curveSensorGroups.forEach((source) => {
    state.curveSensorSelections[source] = (curve.sensor_ids || []).filter((sensorId) => {
      const sensor = state.sensors.find((item) => item.id === sensorId);
      return sensor && sensorSourceName(sensor) === source;
    });
  });
  state.curveTargetControllers = [...new Set((curve.targets || []).map((target) => target.controller_id))].filter(
    (controllerId) => controllerById(controllerId)
  );
  state.collapsedCurveTargets = {};
  state.curveTargetSelections = {};
  state.curveTargetControllers.forEach((controllerId) => {
    const controller = controllerById(controllerId);
    const fans = controller ? fanList(controller) : [];
    const curveFans = curve.targets
      .filter((target) => target.controller_id === controllerId)
      .map((target) => target.fan);
    state.curveTargetSelections[controllerId] = curveFans.includes("all")
      ? fans.map((fan) => fan.id)
      : curveFans;
  });

  renderCurveSensorOptions();
  renderCurveTargets();
  renderCurvePoints(false);
  updateCurveFormMode();
  setCurveFormStatus(`Loaded ${curve.name}. Saving with the same name will update it.`, "ok");
  qs(".curve-editor").scrollIntoView({ behavior: "smooth", block: "start" });
}

function curveTargetRpmRows(targets) {
  if (!targets.length) {
    return [el("div", { className: "item-meta", text: "No fan targets" })];
  }

  const grouped = new Map();
  targets.forEach((target) => {
    const current = grouped.get(target.controller_id) || [];
    current.push(target.fan);
    grouped.set(target.controller_id, current);
  });

  return [...grouped.entries()].map(([controllerId, targetFans]) => {
    const controller = controllerById(controllerId);
    const name = controller ? controllerLabel(controller) : controllerId;
    const fans = controller ? fanList(controller) : [];
    let rpmText = "unavailable";

    if (fans.length) {
      const selectedFans = targetFans.includes("all")
        ? fans
        : fans.filter((fan) => targetFans.includes(fan.id));
      rpmText = selectedFans.length
        ? selectedFans.map((fan) => `${fan.id}: ${fan.rpm ?? "?"} RPM`).join(" - ")
        : "unavailable";
    }

    return el("div", { className: "rpm-row" }, [
      el("div", { className: "rpm-controller", text: `${name}:` }),
      el("div", { className: "rpm-values", text: rpmText }),
    ]);
  });
}

function targetLabel(controllerId) {
  const controller = controllerById(controllerId);
  return controller ? controllerLabel(controller) : controllerId;
}

function selectedFanTargets(root) {
  if (root.id === "curve-targets") {
    syncCurveTargetSelectionsFromDom();
    return state.curveTargetControllers.flatMap((controllerId) => {
      const fans = state.curveTargetSelections[controllerId] || [];
      return fans.filter((fan) => fan !== "all").map((fan) => ({ controller_id: controllerId, fan }));
    });
  }

  const targets = [];
  state.controllers.forEach((controller) => {
    const groupInputs = qsa(root, `input[data-controller="${CSS.escape(controller.identifier)}"][data-fan]:checked`);
    if (!groupInputs.length) return;
    const fanValues = groupInputs.map((input) => input.dataset.fan);
    if (fanValues.includes("all")) {
      targets.push({ controller_id: controller.identifier, fan: "all" });
      return;
    }
    fanValues.forEach((fan) => targets.push({ controller_id: controller.identifier, fan }));
  });
  return targets;
}

function currentCurvePoints() {
  syncCurvePointsFromDom();
  const points = state.curvePoints
    .map((point) => ({ temp: Number(point.temp), pwm: Number(point.pwm) }))
    .filter((point) => Number.isFinite(point.temp) && Number.isFinite(point.pwm));
  if (!points.length) throw new Error("Add at least one curve point.");
  if (qs("#curve-type").value === "flat") {
    return [{ temp: 0, pwm: points[0].pwm }];
  }
  return points;
}

qs("#refresh").addEventListener("click", loadStatus);
qs("#manual-controller").addEventListener("change", renderManualFans);
qs("#manual-pwm").addEventListener("input", (event) => {
  qs("#manual-pwm-value").textContent = `${event.target.value}%`;
});
qs("#curve-type").addEventListener("change", () => {
  updateCurveFormMode();
  renderCurvePoints();
});
qs("#add-curve-point").addEventListener("click", () => {
  syncCurvePointsFromDom();
  state.curvePoints.push({ temp: 60, pwm: 60 });
  renderCurvePoints(false);
});
qs("#add-curve-sensor").addEventListener("click", () => {
  const source = qs("#curve-sensor-add").value;
  if (!source || state.curveSensorGroups.includes(source)) return;
  state.curveSensorGroups.push(source);
  state.collapsedCurveSensors[source] = false;
  state.curveSensorSelections[source] = [];
  renderCurveSensorOptions();
});
qs("#add-switch-sensor").addEventListener("click", () => {
  const source = qs("#switch-sensor-add").value;
  if (!source || state.switchSensorGroups.includes(source)) return;
  state.switchSensorGroups.push(source);
  renderSwitchSensorOptions();
});
qs("#add-curve-controller").addEventListener("click", () => {
  const identifier = qs("#curve-controller-add").value;
  if (!identifier || state.curveTargetControllers.includes(identifier)) return;
  state.curveTargetControllers.push(identifier);
  state.collapsedCurveTargets[identifier] = false;
  state.curveTargetSelections[identifier] = [];
  renderCurveTargets();
});

qs("#source-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/api/sources", {
    method: "POST",
    body: JSON.stringify({
      name: qs("#source-name").value,
      url: qs("#source-url").value,
    }),
  });
  qs("#source-form").reset();
  await loadStatus();
});

qs("#switch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setSwitchFormStatus("");
  try {
    const switchName = qs("#switch-name").value.trim();
    if (!switchName) {
      setSwitchFormStatus("Switch name is required.");
      return;
    }
    const existing = state.powerSwitches.find((powerSwitch) => powerSwitch.name.toLowerCase() === switchName.toLowerCase());
    const saved = await api("/api/power-switches", {
      method: "POST",
      body: JSON.stringify({
        id: existing?.id || switchName,
        name: switchName,
        url: qs("#switch-url").value,
        sensor_groups: state.switchSensorGroups,
      }),
    });
    state.editingSwitchId = saved.id;
    setSwitchFormStatus(existing ? `Updated ${saved.name}.` : `Created ${saved.name}.`, "ok");
    await loadStatus();
  } catch (error) {
    setSwitchFormStatus(error.message || "Unable to save switch.");
  }
});

qs("#manual-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const controllerId = qs("#manual-controller").value;
  const pwm = Number(qs("#manual-pwm").value);
  if (!controllerId) return;

  const selected = selectedFanTargets(qs("#manual-fans"));
  for (const target of selected) {
    const path = target.fan === "all"
      ? `/api/controllers/${encodeURIComponent(target.controller_id)}/fans/all/pwm`
      : `/api/controllers/${encodeURIComponent(target.controller_id)}/fans/${encodeURIComponent(target.fan)}/pwm`;
    await api(path, { method: "POST", body: JSON.stringify({ pwm }) });
  }
  await loadStatus();
});

qs("#curve-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setCurveFormStatus("");
  try {
    const curveType = qs("#curve-type").value;
    const selectedSensors = selectedCurveSensors();
    const targets = selectedFanTargets(qs("#curve-targets"));
    if (curveType !== "flat" && !selectedSensors.length) {
      setCurveFormStatus("Select at least one sensor before saving.");
      return;
    }
    if (!targets.length) {
      setCurveFormStatus("Add a controller and select at least one fan before saving.");
      return;
    }

    const curveName = qs("#curve-name").value.trim();
    if (!curveName) {
      setCurveFormStatus("Curve name is required.");
      return;
    }
    const existing = state.curves.find((curve) => curve.name.toLowerCase() === curveName.toLowerCase());
    const curve = await api("/api/curves", {
      method: "POST",
      body: JSON.stringify({
        id: existing?.id || curveName,
        name: curveName,
        curve_type: curveType,
        sensor_ids: curveType === "flat" ? [] : selectedSensors,
        targets,
        points: currentCurvePoints(),
        hysteresis: Number(qs("#curve-hysteresis").value || 2),
        response_time: Number(qs("#curve-response-time").value || 1),
        enabled: true,
      }),
    });
    setCurveFormStatus(existing ? `Updated ${curve.name}.` : `Created ${curve.name}.`, "ok");
    await loadStatus();
    updateCurveFormMode();
  } catch (error) {
    setCurveFormStatus(error.message || "Unable to save curve.");
  }
});

loadStatus().catch((error) => {
  qs("#summary").textContent = error.message;
});
setInterval(() => loadStatus().catch(() => {}), 2500);
