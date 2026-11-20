-- Exercise the shared marker under modern timers and both legacy frame callback conventions.
local addonPath = "addon/SpokenMediaPause/SpokenMediaPause.lua"
local emitting = arg and arg[1] == "--frames"

local function checkClient(mode)
    local scripts, cells = {}, {}
    local audioChanged, heartbeat, registered, clip, speaking
    local modern = mode == "modern"
    local classicGlobals = mode == "vanilla"
    local mod, unpackValues = math.mod, unpack
    if not classicGlobals then
        -- Standard Lua builds can expose compatibility functions missing in WoW.
        math.mod, unpack = nil, nil
    end
    WorldFrame = not classicGlobals and {} or nil
    UIParent = {}
    SlashCmdList = {}
    SpokenMediaPauseDisabled = nil
    SpokenMediaPauseClassicMarker = nil
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

    local frame = {
        SetWidth = function(self, width) self.width = width end,
        SetHeight = function(self, height) self.height = height end,
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
    function frame:CreateTexture(_, layer)
        local cell = {
            layer = layer,
            SetWidth = function(self, width) self.width = width end,
            SetHeight = function(self, height) self.height = height end,
            SetTexCoord = function(self, left, right, top, bottom)
                self.coords = {left, right, top, bottom}
            end,
            SetPoint = function(self, point, parent, relative, x, y)
                assert(point == "TOPLEFT" and parent == frame and relative == point)
                self.x, self.y = x, -y
            end,
            Show = function(self) self.visible = true end,
            Hide = function(self) self.visible = false end,
        }
        local function color(self, r, g, b, a)
            assert(a == 1)
            self.color = 4 * r + 2 * g + b
            self.rgb = {math.floor(r * 255 + 0.5), math.floor(g * 255 + 0.5), math.floor(b * 255 + 0.5)}
        end
        cell.SetTexture = function(self, value, green, blue, alpha)
            if type(value) == "string" then
                assert(value == "Interface\\AddOns\\SpokenMediaPause\\BrassBadge")
                self.art = true
            else
                color(self, value, green, blue, alpha)
            end
        end
        if modern then cell.SetColorTexture = color end
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

    -- Emit the actual Lua drawing operations for the cross-platform decoder checks.
    local function snapshot(name)
        if not (emitting and modern) then return end
        for _, cell in ipairs(cells) do
            if cell.visible and cell.rgb then
                io.write(string.format("%s\t%s\t%g\t%g\t%g\t%g\t%d\t%d\t%d\n",
                    name, cell.layer, cell.x, cell.y, cell.width, cell.height, cell.rgb[1], cell.rgb[2], cell.rgb[3]))
            end
        end
    end

    local function checkBrass(state, clock)
        local tones = {54, 29, 52, 30, 32}
        local pattern = {2, 0, 2, 1, state, 2 - state, clock == 0 and 3 or 4, 0}
        assert(frame.width == 26 and frame.height == 26 and cells[7].visible and cells[7].art)
        -- The visible art comes from the matching tile, separate from the bevel.
        assert(cells[7].coords[1] == (64 * state + 0.5) / 256)
        assert(cells[7].coords[2] == (64 * (state + 1) - 0.5) / 256)
        for index, value in ipairs(pattern) do
            local cell = cells[8 + index]
            assert(cell.visible and cell.rgb[1] == tones[value + 1] and cell.width == 2.5 and cell.height == 2.75)
            assert(cell.x == 3 + (index - 1) * 2.5 and cell.y == 22.5)
        end
    end

    assert(loadfile(addonPath))()
    checkBrass(0, 0)
    snapshot("brass-idle-dark")
    SpokenMediaPauseDisabled = true
    loadEvent("Spoken")
    assert(registered)
    checkBrass(0, 0)
    loadEvent("SpokenMediaPause")
    assert(not registered)
    checkBrass(2, 0)
    snapshot("brass-off-dark")
    if not modern then
        advance(0.2)
        checkBrass(2, 0)
        advance(0.2)
    else
        advance(0.4)
    end
    checkBrass(2, 2)
    snapshot("brass-off-light")

    clip, speaking = {}, true
    audioChanged()
    checkBrass(2, 2)
    scripts.OnClick()
    assert(not SpokenMediaPauseDisabled)
    checkBrass(1, 2)
    snapshot("brass-narrating-light")
    advance(2)
    checkBrass(1, 0)
    snapshot("brass-narrating-dark")
    clip.textOnly = true
    audioChanged()
    checkBrass(0, 0)
    clip.textOnly, speaking = nil, false
    audioChanged()
    advance(0.4)
    checkBrass(0, 2)
    snapshot("brass-idle-light")

    SlashCmdList.SPOKENMEDIAPAUSE("classic")
    assert(SpokenMediaPauseClassicMarker and frame.width == 27 and frame.height == 18 and not cells[7].visible)
    local classicX = {0, 9, 18, 0, 9, 18}
    for index, color in ipairs({5, 3, 4, 6, 1, 7}) do
        local cell = cells[index]
        assert(cell.visible and cell.color == color and cell.width == 9 and cell.height == 9)
        assert(cell.x == classicX[index] and cell.y == (index <= 3 and 0 or 9))
    end
    snapshot("classic-idle-light")
    advance(0.4)
    assert(cells[6].color == 0)
    SlashCmdList.SPOKENMEDIAPAUSE("brass")
    assert(not SpokenMediaPauseClassicMarker and not cells[1].visible)
    checkBrass(0, 0)
    math.mod, unpack = mod, unpackValues
    if not emitting then print(mode .. " markers passed") end
end

for _, mode in ipairs({ "modern", "wotlk", "vanilla" }) do
    checkClient(mode)
end
