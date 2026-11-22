-- Put Spoken's playback state and the user's pause preference in a small
-- brass badge. Desktop helpers read its lower bevel or the classic marker.
local spoken = _G.Spoken
if not spoken or not spoken.RegisterCallback or not spoken.GetNowPlaying or not spoken.IsPlaying then
    print("Spoken Media Pause needs Spoken or SoftSpoken with the playback callback API.")
    return
end

-- A parentless badge is anchored to the screen so quest-camera addons can
-- fade, hide, or resize the game UI without changing the marker's pixels.
local frame = CreateFrame("Button", "SpokenMediaPauseMarker", nil)
frame:SetPoint("TOPLEFT", nil, "TOPLEFT", 12, -12)
-- Keep the badge's opacity, scale, and draw order independent on clients
-- that expose these controls. Legacy clients use the parentless anchor alone.
if frame.SetIgnoreParentAlpha then frame:SetIgnoreParentAlpha(true) end
if frame.SetIgnoreParentScale then frame:SetIgnoreParentScale(true) end
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(100)
if frame.SetFixedFrameStrata then frame:SetFixedFrameStrata(true) end
if frame.SetFixedFrameLevel then frame:SetFixedFrameLevel(true) end
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

--- Anchor a texture to the badge and include it in a style's visibility group.
local function region(group, layer, x, y, width, height)
    local texture = frame:CreateTexture(nil, layer)
    texture:SetWidth(width)
    texture:SetHeight(height)
    texture:SetPoint("TOPLEFT", frame, "TOPLEFT", x, -y)
    table.insert(group, texture)
    return texture
end

local classic, brass = {}, {}
local classicColors = {{1, 0, 1}, {0, 1, 1}, {1, 0, 0}, {1, 1, 0}, {0, 0, 1}, {0, 0, 0}}
for index, color in ipairs(classicColors) do
    -- Floor-based indexing works on both WoW's Lua and the Lua 5.0 client.
    local row = math.floor((index - 1) / 3)
    local column = index - 1 - 3 * row
    paint(region(classic, "OVERLAY", 9 * column, 9 * row, 9, 9), color[1], color[2], color[3])
end

local plate = region(brass, "BACKGROUND", 0, 0, 26, 26)
plate:SetTexture("Interface\\AddOns\\SpokenMediaPause\\BrassBadge")

-- These dark tones have nearly equal brightness so the code reads as one
-- shadow. The two heartbeat colors are reserved for the clock cell.
-- Layout: 2 0 2 1 mode (2-mode) clock 0.
local palette = {{54, 39, 23}, {29, 43, 47}, {52, 32, 48}, {30, 35, 59}, {32, 42, 29}}
local function tone(texture, index)
    local rgb = palette[index + 1]
    paint(texture, rgb[1] / 255, rgb[2] / 255, rgb[3] / 255)
end

-- Start with a bronze seam so the first cell has a measurable width even over
-- the painted brass. Keeping the strip opaque makes its tones scene-independent.
tone(region(brass, "ARTWORK", 2, 22.5, 1, 2.75), 0)
local bevel = {}
for index, value in ipairs({2, 0, 2, 1, 0, 2, 3, 0}) do
    bevel[index] = region(brass, "OVERLAY", 3 + 2.5 * (index - 1), 22.5, 2.5, 2.75)
    tone(bevel[index], value)
end

local heartbeat = 0
local usingClassic = false
local displayedMode

--- Paint only the visible heartbeat. It is independent of narration.
local function paintClock()
    if usingClassic then
        paint(classic[6], heartbeat, heartbeat, heartbeat)
    else
        tone(bevel[7], 3 + heartbeat)
    end
end

--- Publish the saved preference before inspecting playback. Clicking the marker
--- does not change Spoken's queue. Its heartbeat keeps running while disabled.
local function update()
    local mode = 0
    if SpokenMediaPauseDisabled then
        mode = 2
    else
        -- The post-mutation callback sees the clip with its timer installed.
        local clip = spoken:GetNowPlaying()
        if clip and not clip.textOnly and spoken:IsPlaying(clip) then
            mode = 1
        end
    end
    -- Audio callbacks can repeat without a state change. Only repaint the
    -- visible controls when their mode changes or a style is applied.
    if mode == displayedMode then return end
    displayedMode = mode
    if usingClassic then
        paint(classic[3], mode == 1 and 0 or 1, mode == 0 and 0 or 1, mode == 2 and 1 or 0)
        return
    end

    -- The atlas packs Idle, Narrating and Off on one texture. A half-texel
    -- inset keeps filtering within the selected sprite.
    plate:SetTexCoord((64 * mode + 0.5) / 256, (64 * (mode + 1) - 0.5) / 256, 0.5 / 64, 63.5 / 64)
    tone(bevel[5], mode)
    tone(bevel[6], 2 - mode)
end

--- Apply the saved appearance and synchronize it before the next capture.
local function applyStyle()
    usingClassic = SpokenMediaPauseClassicMarker == true
    displayedMode = nil
    frame:SetWidth(usingClassic and 27 or 26)
    frame:SetHeight(usingClassic and 18 or 26)
    for _, texture in ipairs(classic) do
        if usingClassic then texture:Show() else texture:Hide() end
    end
    for _, texture in ipairs(brass) do
        if usingClassic then texture:Hide() else texture:Show() end
    end
    paintClock()
    update()
end

frame:SetScript("OnClick", function()
    SpokenMediaPauseDisabled = not SpokenMediaPauseDisabled
    update()
end)
frame:SetScript("OnEnter", function()
    if GameTooltip then
        GameTooltip:SetOwner(frame, "ANCHOR_BOTTOMRIGHT")
        GameTooltip:SetText(SpokenMediaPauseDisabled and "Click to enable media auto-pause" or "Click to disable media auto-pause")
        GameTooltip:Show()
    end
end)
frame:SetScript("OnLeave", function()
    if GameTooltip then GameTooltip:Hide() end
end)

-- Older desktop helpers can still use the classic colors via this fallback.
SLASH_SPOKENMEDIAPAUSE1 = "/smp"
SlashCmdList.SPOKENMEDIAPAUSE = function(message)
    local style = string.lower(string.gsub(message or "", "^%s*(.-)%s*$", "%1"))
    if style == "classic" or style == "brass" then
        SpokenMediaPauseClassicMarker = style == "classic"
        applyStyle()
    else
        print("Spoken Media Pause: /smp brass or /smp classic")
    end
end

spoken:RegisterCallback("AUDIO_CHANGED", update)
applyStyle()

-- Values from SavedVariables are available by this addon's own ADDON_LOADED.
-- Vanilla 1.12 exposes the addon name as arg1 rather than an event argument.
frame:RegisterEvent("ADDON_LOADED")
frame:SetScript("OnEvent", function(_, eventName, addonName)
    if (eventName or event) == "ADDON_LOADED" and (addonName or arg1) == "SpokenMediaPause" then
        frame:UnregisterEvent("ADDON_LOADED")
        applyStyle()
    end
end)

--- Flip the clock cell once per tick so both desktop helpers can detect stale frames.
local function tick()
    heartbeat = 1 - heartbeat
    paintClock()
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
