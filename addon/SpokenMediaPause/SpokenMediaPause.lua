-- Expose Spoken's playback state and the user's pause preference as a two-row
-- color marker. The desktop helper reads only those states from the game window.
local spoken = _G.Spoken
if not spoken or not spoken.RegisterCallback or not spoken.GetNowPlaying or not spoken.IsPlaying then
    print("Spoken Media Pause needs a Spoken version with the playback callback API (such as 3.8.7).")
    return
end

-- Layout, left to right:
--   magenta, cyan,   red (idle) / green (narrating) / white (disabled)
--   yellow,  blue,   black/white (heartbeat)
-- The fixed colors distinguish the marker from scenery. The heartbeat lets the
-- receiver release paused media if the captured image freezes or WoW closes.
local cell = 9
-- DynamicCam fades UIParent and can hide it outright. WorldFrame stays visible
-- through both paths. Anchor to the screen corner independently of UIParent.
local frame = CreateFrame("Button", "SpokenMediaPauseMarker", WorldFrame)
frame:SetSize(cell * 3, cell * 2)
frame:SetPoint("TOPLEFT", WorldFrame, "TOPLEFT", 12, -12)
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(100)
frame:EnableMouse(true)
frame:RegisterForClicks("LeftButtonUp")

--- Create one opaque square at a marker row and column, both zero-based.
local function square(column, row, red, green, blue)
    local texture = frame:CreateTexture(nil, "OVERLAY")
    texture:SetSize(cell, cell)
    texture:SetPoint("TOPLEFT", frame, "TOPLEFT", column * cell, -row * cell)
    texture:SetColorTexture(red, green, blue, 1)
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
        state:SetColorTexture(1, 1, 1, 1)
        return
    end

    -- The post-mutation callback sees the clip with its playback timer installed.
    -- A text-only or held clip does not need to interrupt the browser.
    local clip = spoken:GetNowPlaying()
    if clip and not clip.textOnly and spoken:IsPlaying(clip) then
        state:SetColorTexture(0, 1, 0, 1)
    else
        state:SetColorTexture(1, 0, 0, 1)
    end
end

frame:SetScript("OnClick", function()
    SpokenMediaPauseDisabled = not SpokenMediaPauseDisabled
    update()
end)
spoken:RegisterCallback("AUDIO_CHANGED", update)
update()

-- One inexpensive timer for the UI lifetime, independent of the queue or FPS.
C_Timer.NewTicker(0.4, function()
    heartbeat = 1 - heartbeat
    clock:SetColorTexture(heartbeat, heartbeat, heartbeat, 1)
end)
