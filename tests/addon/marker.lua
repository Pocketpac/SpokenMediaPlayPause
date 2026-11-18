-- Exercise the shared marker under modern timers and both legacy frame callback conventions.
local addonPath = "addon/SpokenMediaPause/SpokenMediaPause.lua"

local function checkClient(mode)
    local scripts, cells = {}, {}
    local audioChanged, heartbeat, registered, clip, speaking
    local modern = mode == "modern"
    local classicGlobals = mode == "vanilla"
    WorldFrame = not classicGlobals and {} or nil
    UIParent = {}
    SpokenMediaPauseDisabled = nil
    C_Timer = nil
    if modern then
        C_Timer = {NewTicker = function(interval, callback)
            assert(interval == 0.4)
            heartbeat = callback
        end}
    end

    Spoken = {
        RegisterCallback = function(_, name, callback)
            assert(name == "AUDIO_CHANGED" and not audioChanged)
            audioChanged = callback
        end,
        GetNowPlaying = function() return clip end,
        IsPlaying = function(_, item) return item == clip and speaking end,
    }

    local cellIndex = 0
    local frame = {
        SetWidth = function(_, width) assert(width == 27) end,
        SetHeight = function(_, height) assert(height == 18) end,
        SetPoint = function(_, point, parent, relative, x, y)
            assert(point == "TOPLEFT" and parent == (WorldFrame or UIParent) and relative == point)
            assert(x == 12 and y == -12)
        end,
        SetFrameStrata = function() end,
        SetFrameLevel = function() end,
        EnableMouse = function() end,
        RegisterForClicks = function(_, button) assert(button == "LeftButtonUp") end,
        SetScript = function(_, name, callback) scripts[name] = callback end,
        RegisterEvent = function(_, name) assert(name == "ADDON_LOADED"); registered = true end,
        UnregisterEvent = function(_, name) assert(name == "ADDON_LOADED"); registered = false end,
    }
    function frame:CreateTexture()
        local column, row = math.mod(cellIndex, 3), math.floor(cellIndex / 3)
        cellIndex = cellIndex + 1
        local cell = {
            SetWidth = function(_, width) assert(width == 9) end,
            SetHeight = function(_, height) assert(height == 9) end,
            SetPoint = function(_, point, parent, relative, x, y)
                assert(point == "TOPLEFT" and parent == frame and relative == point)
                assert(x == column * 9 and y == -row * 9)
            end,
        }
        local function color(self, r, g, b, a)
            assert(a == 1)
            self.color = 4 * r + 2 * g + b
        end
        if modern then cell.SetColorTexture = color else cell.SetTexture = color end
        table.insert(cells, cell)
        return cell
    end

    CreateFrame = function(kind, name, parent)
        assert(kind == "Button" and name == "SpokenMediaPauseMarker" and parent == (WorldFrame or UIParent))
        return frame
    end

    local function loadEvent(name)
        if classicGlobals then
            event, arg1 = "ADDON_LOADED", name
            scripts.OnEvent()
            event, arg1 = nil, nil
        else
            scripts.OnEvent(frame, "ADDON_LOADED", name)
        end
    end

    local function advance(elapsed)
        if modern then
            heartbeat()
        elseif classicGlobals then
            arg1 = elapsed
            scripts.OnUpdate()
            arg1 = nil
        else
            scripts.OnUpdate(frame, elapsed)
        end
    end

    assert(loadfile(addonPath))()
    for index, color in ipairs({5, 3, 4, 6, 1, 0}) do
        assert(cells[index].color == color)
    end

    SpokenMediaPauseDisabled = true
    loadEvent("Spoken")
    assert(registered and cells[3].color == 4)
    loadEvent("SpokenMediaPause")
    assert(not registered and cells[3].color == 7)

    clip, speaking = {}, true
    audioChanged()
    assert(cells[3].color == 7)
    scripts.OnClick()
    assert(not SpokenMediaPauseDisabled and cells[3].color == 2)
    clip.textOnly = true
    audioChanged()
    assert(cells[3].color == 4)
    clip.textOnly, speaking = nil, false
    audioChanged()
    assert(cells[3].color == 4)

    if not modern then
        advance(0.2)
        assert(cells[6].color == 0)
        advance(0.2)
    else
        advance(0.4)
    end
    assert(cells[6].color == 7)
    advance(2)
    assert(cells[6].color == 0)
    print(mode .. " marker passed")
end

for _, mode in ipairs({ "modern", "wotlk", "vanilla" }) do
    checkClient(mode)
end
