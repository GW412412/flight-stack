-- EXAMPLE SKETCH ONLY — test thoroughly in SITL before any real flight.
-- Intent: keep sprayer OFF when vehicle is in RTL or Land.
-- Aux function 15 = Sprayer (see ArduPilot auxiliary functions docs).
-- Pattern discussed in community / PR context around AC_Sprayer RTL behavior:
--   https://github.com/ArduPilot/ardupilot/pull/28684
--
-- Enable scripting: SCR_ENABLE = 1
-- Copy to APM/scripts/ on the SD card (or SITL scripts folder).
--
-- Mode numbers differ by vehicle; verify for your Copter firmware version.
-- This sketch uses vehicle:get_mode() and forces aux sprayer low when mode
-- matches common Copter RTL/Land mode numbers — ADJUST after checking your
-- mode list (Mission Planner Full Parameter / docs).

local SPRAYER_AUX = 15
local SWITCH_LOW = 0   -- stop
-- local SWITCH_HIGH = 2 -- run (unused here)

-- Copter mode numbers commonly used (VERIFY for your version):
local MODE_RTL = 6
local MODE_LAND = 9

local function update()
  local mode = vehicle:get_mode()
  if mode == MODE_RTL or mode == MODE_LAND then
    -- Force sprayer aux function to low (off)
    rc:run_aux_function(SPRAYER_AUX, SWITCH_LOW)
  end
  return update, 200  -- 5 Hz
end

gcs:send_text(6, "example-sprayer-rtl-off: loaded (EXAMPLE)")
return update, 1000
