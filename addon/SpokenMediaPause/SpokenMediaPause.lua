-- Expose Spoken's playback state and the user's pause preference as a two-row
-- color marker. The desktop helper reads only those states from the game window.
local spoken = _G.Spoken
if not spoken or not spoken.RegisterCallback or not spoken.GetNowPlaying or not spoken.IsPlaying then
    print("Spoken Media Pause needs Spoken or SoftSpoken with the playback callback API.")
    return
end

-- Layout, left to right:
--   magenta, cyan,   red (idle) / green (narrating) / white (disabled)
--   yellow,  blue,   black/white (heartbeat)
-- The fixed colors distinguish the marker from scenery. The heartbeat lets the
-- receiver release paused media if the captured image freezes or WoW closes.
local cell = 9
-- Prefer WorldFrame so the marker survives UIParent fades and hides.
-- Clients without WorldFrame use UIParent as the corner anchor.
local parent = WorldFrame or UIParent
local frame = CreateFrame("Button", "SpokenMediaPauseMarker", parent)
frame:SetWidth(cell * 3)
frame:SetHeight(cell * 2)
frame:SetPoint("TOPLEFT", parent, "TOPLEFT", 12, -12)
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(100)
frame:EnableMouse(true)
frame:RegisterForClicks("LeftButtonUp")

--- Paint an opaque RGB cell using the solid color API available on this client.
local function paint(texture, red, green, blue)
    if texture.SetColorTexture then
        texture:SetColorTexture(red, green, blue, 1)
    else
        texture:SetTexture(red, green, blue, 1)
    end
end

--- Create one opaque square at a marker row and column, both zero-based.
local function square(column, row, red, green, blue)
    local texture = frame:CreateTexture(nil, "OVERLAY")
    texture:SetWidth(cell)
    texture:SetHeight(cell)
    texture:SetPoint("TOPLEFT", frame, "TOPLEFT", column * cell, -row * cell)
    paint(texture, red, green, blue)
    return texture
end

square(0, 0, 1, 0, 1)
square(1, 0, 0, 1, 1)
local state = square(2, 0, 1, 0, 0)
square(0, 1, 1, 1, 0)
square(1, 1, 0, 0, 1)
local clock = square(2, 1, 0, 0, 0)
local heartbeat = 0

--- Publish the saved preference before inspecting playback. Clicking the marker
--- does not change Spoken's queue. Its heartbeat keeps running while disabled.
local function update()
    if SpokenMediaPauseDisabled then
        paint(state, 1, 1, 1)
        return
    end

    -- The post-mutation callback sees the clip with its playback timer installed.
    -- A text-only or held clip does not need to interrupt the browser.
    local clip = spoken:GetNowPlaying()
    if clip and not clip.textOnly and spoken:IsPlaying(clip) then
        paint(state, 0, 1, 0)
    else
        paint(state, 1, 0, 0)
    end
end

frame:SetScript("OnClick", function()
    SpokenMediaPauseDisabled = not SpokenMediaPauseDisabled
    update()
end)
spoken:RegisterCallback("AUDIO_CHANGED", update)
update()

-- Values from SavedVariables are available by this addon's own ADDON_LOADED.
-- Vanilla 1.12 exposes the addon name as arg1 rather than an event argument.
frame:RegisterEvent("ADDON_LOADED")
frame:SetScript("OnEvent", function(_, eventName, addonName)
    if (eventName or event) == "ADDON_LOADED" and (addonName or arg1) == "SpokenMediaPause" then
        frame:UnregisterEvent("ADDON_LOADED")
        update()
    end
end)

--- Flip the clock cell once per tick so both desktop helpers can detect stale frames.
local function tick()
    heartbeat = 1 - heartbeat
    paint(clock, heartbeat, heartbeat, heartbeat)
end

local period = 0.4
if C_Timer and C_Timer.NewTicker then
    C_Timer.NewTicker(period, tick)
else
    -- Legacy clients without C_Timer supply elapsed time as an argument or as arg1.
    -- Flip once after a long frame gap so the next capture can see the change.
    local elapsedSinceTick = 0
    frame:SetScript("OnUpdate", function(_, elapsed)
        elapsedSinceTick = elapsedSinceTick + (elapsed or arg1 or 0)
        if elapsedSinceTick >= period then
            elapsedSinceTick = 0
            tick()
        end
    end)
end
