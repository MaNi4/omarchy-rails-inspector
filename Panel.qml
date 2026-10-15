import QtQuick
import Quickshell
import Quickshell.Io
import "Store.js" as Store
import "Model.js" as Model
import qs.Ui
import qs.Commons

Panel {
  id: root

  moduleName: "mani4.rails-inspector"
  ipcTarget: "mani4.rails-inspector"

  readonly property string ff: bar ? bar.fontFamily : Style.font.family
  readonly property color dimForeground: Qt.darker(barForeground, 1.4)
  readonly property color accent: Color.accent
  readonly property color urgent: Color.urgent

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---------- Service ----------
  property var service: null

  function bindService() {
    if (service) return
    var host = bar && bar.shell ? bar.shell : null
    var s = host && typeof host.serviceFor === "function" ? host.serviceFor(moduleName) : null
    if (!s) s = Store.get()
    if (s) service = s
  }

  Timer {
    interval: 100
    repeat: true
    running: root.service === null
    triggeredOnStart: true
    onTriggered: root.bindService()
  }

  // ---------- Data ----------
  readonly property var status: service ? service.status : ({ ok: true, error: "", js: {}, message: null, projects: [] })
  readonly property var projects: status.projects || []
  readonly property var js: status.js || ({})
  readonly property var message: status.message || null

  property string wantProject: ""
  readonly property var project: Model.chooseProject(projects, wantProject)
  readonly property string projectName: project ? project.name : ""
  onProjectNameChanged: { resetView(); checkSetup() }

  readonly property int alarmTotal: Model.alarmTotal(projects)
  readonly property var projectOptions: Model.projectOptions(projects)

  property string tab: "error"
  onTabChanged: { resetView(); checkSetup() }

  readonly property var rows: Model.rowsOf(project, tab)

  // ---------- View state ----------
  property int cursorIndex: 0
  property bool cursorActive: false
  property string expanded: ""
  property string copied: ""

  function resetView() { cursorIndex = 0; listView.contentY = 0; expanded = ""; copied = "" }

  property real now: Date.now() / 1000

  Timer {
    interval: 30000
    repeat: true
    running: root.opened
    onTriggered: root.now = Date.now() / 1000
  }

  // ---------- Commands ----------
  function detailOf(fp) {
    return service ? service.details[Model.detailKey(projectName, fp)] : undefined
  }

  function fetchDetail(p) {
    if (p && service) service.requestDetail(projectName, p.fp)
  }

  function toggleRow(p) {
    if (!p) return
    expanded = expanded === p.fp ? "" : p.fp
    copied = ""
    if (expanded !== "") fetchDetail(p)
  }

  function copyText(text) {
    if (copier.running) return
    copier.text = text
    copier.stdinEnabled = true
    copier.running = true
  }

  property var pendingCopy: null

  function copyRow(p) {
    if (!p) return
    var detail = detailOf(p.fp)
    if (detail === undefined) {
      pendingCopy = p
      fetchDetail(p)
      return
    }
    pendingCopy = null
    copyText(Model.copyText(p, detail))
    copied = p.fp
  }

  Connections {
    target: root.service
    function onDetailsChanged() {
      if (root.pendingCopy && root.detailOf(root.pendingCopy.fp) !== undefined) root.copyRow(root.pendingCopy)
    }
  }

  Process {
    id: copier
    property string text: ""
    command: ["/usr/bin/wl-copy"]
    stdinEnabled: true
    onStarted: { write(text); stdinEnabled = false }
  }

  function openRow(p) {
    if (!p || p.openable !== true || !service) return
    service.open(projectName, p.fp)
  }
  function resolveRow(p) { if (p && service) service.resolve(projectName, p.fp) }
  function ignoreRow(p) { if (p && service) service.ignore(projectName, p.fp) }
  function clearTab() { if (service && project) service.clear(projectName, tab) }
  function scan() { if (service && project) service.scan(projectName) }
  function addProject() { if (service) service.addProject() }
  function removeProject() { if (service && project) service.removeProject(projectName) }
  // ---------- Settings ----------
  property bool configuring: false
  readonly property var settings: status.settings || ({})
  readonly property string originsText: Model.originsText(settings)
  readonly property var ignoredRows: Model.ignoredRows(projects)
  onOriginsTextChanged: if (!originsField.activeFocus) originsField.text = originsText

  function configure() {
    configuring = !configuring
    originsField.text = originsText
  }
  function leaveSettings() { configuring = false }
  function setSetting(key, value) { if (service) service.setSetting(key, String(value)) }
  function saveOrigins() { if (originsField.text.trim() !== originsText) setSetting("jsOrigins", originsField.text.trim()) }

  readonly property var setup: service && projectName ? service.setups[projectName] || null : null
  readonly property var guideSteps: tab === "js" ? Model.guideSteps(setup) : []

  function checkSetup() { if (service && project && opened && tab === "js") service.requestSetup(projectName) }
  onServiceChanged: checkSetup()
  readonly property string jsState: js.port + ":" + js.listening
  onJsStateChanged: checkSetup()

  readonly property var current: cursorActive ? rows[cursorIndex] || null : null

  function moveCursor(dx, dy) {
    if (dx !== 0) {
      tab = Model.nextTab(tab, dx)
      return
    }
    if (!rows.length) return
    var next = Model.moveCursor({ active: cursorActive, index: cursorIndex }, dy, rows.length)
    cursorActive = next.active
    cursorIndex = next.index
  }

  onOpenedChanged: {
    if (opened) {
      cursorActive = false
      cursorIndex = 0
      now = Date.now() / 1000
      var first = Model.firstFreshTab(project)
      if (first) tab = first
      checkSetup()
    } else {
      projectPicker.close()
      if (service && project) service.markSeen(projectName)
    }
  }

  // ---------- Bar icon ----------
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\u{f00e4}" // nf-md-bug
    active: root.alarmTotal > 0
    dimmed: !root.projects.length
    tooltipText: Model.tooltip(root.projects)
    onPressed: function(b) {
      if (b === Qt.RightButton) {
        if (root.service) for (var i = 0; i < root.projects.length; i++) root.service.markSeen(root.projects[i].name)
      } else root.toggle()
    }
  }

  // ---------- Pieces ----------
  component ProblemRow: Rectangle {
    id: row
    property var entry: ({})
    property bool hasCursor: false
    readonly property bool hot: rowMouse.containsMouse || hasCursor
    readonly property bool unfolded: root.expanded === entry.fp
    readonly property bool fresh: Model.isFresh(root.project, entry)
    readonly property real lastSeen: Number(entry.last || 0)
    onLastSeenChanged: if (unfolded) root.fetchDetail(entry)

    width: parent ? parent.width : 0
    implicitHeight: rowText.implicitHeight + Style.spacing.controlPaddingY * 2
    radius: Style.space(4)
    color: hot ? Style.hoverFillFor(root.barForeground, root.accent) : "transparent"

    Rectangle {
      visible: row.fresh
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      anchors.margins: Style.space(3)
      width: Style.space(2)
      radius: width / 2
      color: Model.isAlarm(row.entry) ? root.urgent : root.accent
    }

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: root.toggleRow(row.entry)
    }

    Column {
      id: rowText
      anchors.left: parent.left
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.right: rowActions.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: row.entry.title || ""
        elide: Text.ElideRight
        color: Model.isAlarm(row.entry) ? root.urgent : root.barForeground
        font.family: root.ff
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: Model.subtitle(row.entry, root.now)
        elide: Text.ElideMiddle
        color: root.dimForeground
        font.family: root.ff
        font.pixelSize: Style.font.caption
      }

      Text {
        visible: row.unfolded && text !== ""
        width: parent.width
        textFormat: Text.PlainText
        text: {
          var d = root.detailOf(row.entry.fp)
          return d === undefined ? "Loading…" : d || ""
        }
        wrapMode: Text.WrapAnywhere
        maximumLineCount: 80
        elide: Text.ElideRight
        color: root.barForeground
        font.family: root.ff
        font.pixelSize: Style.font.caption
        topPadding: Style.space(4)
      }

      Text {
        visible: row.unfolded && text !== ""
        width: parent.width
        textFormat: Text.PlainText
        text: Model.history(row.entry, root.now)
        color: root.dimForeground
        font.family: root.ff
        font.pixelSize: Style.font.caption
      }
    }

    Row {
      id: rowActions
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.controlPaddingX
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(4)

      PanelActionButton {
        visible: row.entry.openable === true
        iconText: "\u{f03eb}" // nf-md-pencil
        tooltipText: "Open " + (row.entry.where || "") + " in the editor"
        foreground: root.barForeground
        fontFamily: root.ff
        onClicked: root.openRow(row.entry)
      }
      PanelActionButton {
        iconText: root.copied === row.entry.fp ? "\u{f012c}" : "\u{f018f}" // nf-md-check, nf-md-content_copy
        tooltipText: "Copy the whole problem"
        foreground: root.barForeground
        fontFamily: root.ff
        onClicked: root.copyRow(row.entry)
      }
      PanelActionButton {
        iconText: "\u{f05e0}" // nf-md-check_circle_outline
        tooltipText: "Resolved: remove it; it comes back if it happens again"
        foreground: root.barForeground
        fontFamily: root.ff
        onClicked: root.resolveRow(row.entry)
      }
      PanelActionButton {
        iconText: "\u{f0209}" // nf-md-eye_off
        tooltipText: "Ignore: never show this one again"
        foreground: root.barForeground
        hoverColor: root.urgent
        fontFamily: root.ff
        onClicked: root.ignoreRow(row.entry)
      }
    }
  }

  component SettingSwitch: Rectangle {
    id: setting
    property string label: ""
    property bool checked: false
    signal toggled()

    width: parent ? parent.width : 0
    implicitHeight: Math.max(settingLabel.implicitHeight, settingToggle.implicitHeight) + Style.spacing.controlPaddingY * 2
    radius: Style.space(4)
    color: settingMouse.containsMouse ? Style.hoverFillFor(root.barForeground, root.accent) : "transparent"

    Text {
      id: settingLabel
      anchors.left: parent.left
      anchors.leftMargin: Style.spacing.controlPaddingX
      anchors.right: settingToggle.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: setting.label
      color: root.barForeground
      font.family: root.ff
      font.pixelSize: Style.font.bodySmall
    }

    MouseArea {
      id: settingMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: setting.toggled()
    }

    ToggleSwitch {
      id: settingToggle
      anchors.right: parent.right
      anchors.rightMargin: Style.spacing.controlPaddingX
      anchors.verticalCenter: parent.verticalCenter
      checked: setting.checked
      foreground: root.barForeground
      onToggled: setting.toggled()
    }
  }

  component SetupStep: Column {
    id: stepView
    property var entry: ({})
    spacing: Style.space(4)

    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      textFormat: Text.PlainText
      text: (stepView.entry.ok ? "\u{f05e0}  " : stepView.entry.optional ? "\u{f0028}  " : "\u{f0159}  ") + (stepView.entry.title || "") // nf-md-check_circle_outline, nf-md-alert_circle, nf-md-close_circle
      color: stepView.entry.ok ? root.dimForeground : stepView.entry.optional ? root.accent : root.urgent
      font.family: root.ff
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      visible: text !== ""
      width: parent.width
      wrapMode: Text.WordWrap
      textFormat: Text.PlainText
      text: [stepView.entry.file, stepView.entry.note].filter(function(x) { return !!x }).join("  ·  ")
      color: root.dimForeground
      font.family: root.ff
      font.pixelSize: Style.font.caption
    }

    Rectangle {
      visible: !!stepView.entry.code
      width: parent.width
      implicitHeight: Math.max(codeText.y + codeText.implicitHeight, codeCopy.y + codeCopy.height) + Style.spacing.controlPaddingY
      radius: Style.space(4)
      color: Qt.rgba(root.barForeground.r, root.barForeground.g, root.barForeground.b, 0.06)

      FontMetrics {
        id: codeMetrics
        font: codeText.font
      }

      TextEdit {
        id: codeText
        anchors.left: parent.left
        anchors.right: codeCopy.left
        anchors.leftMargin: Style.spacing.controlPaddingX
        anchors.rightMargin: Style.space(8)
        anchors.top: parent.top
        anchors.topMargin: Style.spacing.controlPaddingY + Math.max(0, (codeCopy.height - codeMetrics.height) / 2)
        readOnly: true
        selectByMouse: true
        persistentSelection: true
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.WrapAnywhere
        text: stepView.entry.code || ""
        color: root.barForeground
        selectionColor: root.accent
        selectedTextColor: Color.background
        font.family: root.ff
        font.pixelSize: Style.font.caption
      }

      PanelActionButton {
        id: codeCopy
        anchors.right: parent.right
        anchors.rightMargin: Style.spacing.controlPaddingX
        anchors.top: parent.top
        anchors.topMargin: Style.spacing.controlPaddingY
        iconText: root.copied === "setup:" + stepView.entry.key ? "\u{f012c}" : "\u{f018f}" // nf-md-check, nf-md-content_copy
        tooltipText: "Copy"
        foreground: root.barForeground
        fontFamily: root.ff
        onClicked: {
          root.copyText(stepView.entry.code)
          root.copied = "setup:" + stepView.entry.key
        }
      }
    }
  }

  // ---------- Panel ----------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(760))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      clip: true
      blocked: projectPicker.popupOpen
      onCloseRequested: root.configuring ? root.leaveSettings() : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) { if (!root.configuring) root.moveCursor(dx, dy) }
      onActivateRequested: if (!root.configuring) root.current && root.current.openable === true ? root.openRow(root.current) : root.toggleRow(root.current)
      onDeleteRequested: if (!root.configuring) root.resolveRow(root.current)
      onTextKey: function(t) {
        var k = t.toLowerCase()
        if (k === "c") root.configure()
        else if (root.configuring) return
        else if (k === "o") root.toggleRow(root.current)
        else if (k === "y") root.copyRow(root.current)
        else if (k === "x") root.resolveRow(root.current)
        else if (k === "i") root.ignoreRow(root.current)
        else if (k === "p") projectPicker.open()
        else if (k === "a") root.addProject()
        else if (k === "s") root.scan()
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(10)

        // ---------- Masthead ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(masthead.implicitHeight, edition.implicitHeight)

          PanelSectionHeader {
            id: masthead
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "RAILS INSPECTOR"
            foreground: root.barForeground
            fontFamily: root.ff
          }

          Text {
            id: edition
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: Model.settingsLine(root.status.thresholds, root.js)
            color: root.js.error ? root.urgent : root.dimForeground
            font.family: root.ff
            font.pixelSize: Style.font.caption
          }
        }

        // ---------- Project ----------
        Row {
          visible: !root.configuring
          width: parent.width
          spacing: Style.space(10)

          SearchableDropdown {
            id: projectPicker
            width: Math.min(Style.space(280), parent.width / 2)
            anchors.verticalCenter: parent.verticalCenter
            showLabel: false
            placeholderText: "Search projects…"
            emptyText: "No such project"
            fontFamily: root.ff
            foreground: root.barForeground
            value: root.projectName
            options: root.projectOptions
            onChanged: function(name) {
              if (root.service && root.project && name !== root.projectName) root.service.markSeen(root.projectName)
              root.wantProject = name
              value = Qt.binding(function() { return root.projectName })
            }
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - projectPicker.width - parent.spacing
            textFormat: Text.PlainText
            elide: root.project && root.project.notice ? Text.ElideRight : Text.ElideLeft
            text: root.project ? (root.project.error || root.project.notice || root.project.log.replace(root.project.path + "/", "")) : ""
            color: root.project && (root.project.error || root.project.notice) ? root.urgent : root.dimForeground
            font.family: root.ff
            font.pixelSize: Style.font.caption
          }
        }

        Text {
          visible: !root.configuring && text !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: !root.service || !root.service.started ? "Starting…"
            : root.status.error ? root.status.error
            : !root.projects.length ? "No projects yet. Press Add project and choose a Rails application."
            : ""
          color: root.status.error ? root.urgent : root.dimForeground
          font.family: root.ff
          font.pixelSize: Style.font.bodySmall
        }

        // ---------- Tabs ----------
        Flow {
          visible: !root.configuring && root.project !== null
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: Model.KINDS
            Button {
              required property var modelData
              readonly property int total: Model.countIn(root.project, modelData.key)
              readonly property int fresh: Model.freshIn(root.project, modelData.key)
              text: Model.tabLabel(modelData, root.project)
              selected: root.tab === modelData.key
              fontSize: Style.font.bodySmall
              foreground: fresh > 0 && Model.ALARMING.indexOf(modelData.key) >= 0 ? root.urgent
                        : total > 0 ? root.barForeground : root.dimForeground
              fontFamily: root.ff
              bordered: true
              horizontalPadding: Style.spacing.controlPaddingX
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: root.tab = modelData.key
            }
          }
        }

        Text {
          visible: !root.configuring && root.project !== null && !root.rows.length && text !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: Model.emptyText(root.tab, root.setup, root.project, root.now)
          color: root.dimForeground
          font.family: root.ff
          font.pixelSize: Style.font.bodySmall
        }

        // ---------- Browser setup ----------
        Flickable {
          id: setupView
          visible: !root.configuring && root.guideSteps.length > 0
          width: parent.width
          height: Math.min(setupColumn.implicitHeight, Math.max(Style.space(160), panel.availableCardHeight - Style.space(260)))
          contentHeight: setupColumn.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds

          Column {
            id: setupColumn
            width: parent.width
            spacing: Style.space(10)

            Item {
              width: parent.width
              implicitHeight: Math.max(setupHeading.implicitHeight, recheck.implicitHeight)

              Text {
                id: setupHeading
                anchors.left: parent.left
                anchors.right: recheck.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: Model.setupHeading(root.projectName)
                color: root.barForeground
                font.family: root.ff
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }

              PanelActionButton {
                id: recheck
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                iconText: "\u{f0450}" // nf-md-refresh
                tooltipText: "Check the project again"
                foreground: root.barForeground
                fontFamily: root.ff
                onClicked: root.checkSetup()
              }
            }

            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: Model.SETUP_HINT
              color: root.dimForeground
              font.family: root.ff
              font.pixelSize: Style.font.caption
            }

            Repeater {
              model: root.guideSteps
              SetupStep {
                required property var modelData
                width: setupColumn.width
                entry: modelData
              }
            }
          }
        }

        // ---------- Rows ----------
        Flickable {
          id: listView
          visible: !root.configuring && root.rows.length > 0
          width: parent.width
          height: Math.min(list.implicitHeight, Math.max(Style.space(140), panel.availableCardHeight - Style.space(260)))
          contentHeight: list.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds

          function reveal(row) {
            if (!row) return
            if (row.y < contentY) contentY = row.y
            else if (row.y + row.height > contentY + height) contentY = row.y + row.height - height
          }

          Column {
            id: list
            width: parent.width
            spacing: Style.space(2)

            Repeater {
              model: root.rows.length
              ProblemRow {
                required property int index
                entry: root.rows[index] || ({})
                hasCursor: root.cursorActive && root.cursorIndex === index
                onHasCursorChanged: if (hasCursor) listView.reveal(this)
              }
            }
          }
        }

        Button {
          visible: !root.configuring && root.rows.length > 0
          iconText: "\u{f0b5c}" // nf-md-broom
          iconSize: Style.font.icon
          text: "Clear this tab"
          tooltipText: "Remove everything in this tab; what happens again comes back"
          fontSize: Style.font.bodySmall
          foreground: root.barForeground
          fontFamily: root.ff
          bordered: true
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY
          onClicked: root.clearTab()
        }

        // ---------- Settings ----------
        Column {
          visible: root.configuring
          width: parent.width
          spacing: Style.space(10)

          SettingSwitch {
            label: "Desktop notifications for new errors"
            checked: root.settings.notify !== false
            onToggled: root.setSetting("notify", !checked)
          }

          PanelSectionHeader {
            text: "WHAT COUNTS AS A PROBLEM"
            foreground: root.barForeground
            fontFamily: root.ff
          }

          Flow {
            width: parent.width
            spacing: Style.space(14)

            Repeater {
              model: Model.THRESHOLD_SETTINGS
              NumberField {
                required property var modelData
                label: modelData.label
                from: modelData.from
                to: modelData.to
                value: Number(root.settings[modelData.key] || modelData.from)
                foreground: root.barForeground
                fontFamily: root.ff
                fontSize: Style.font.bodySmall
                field.textFromValue: function(v) { return String(v) }
                field.valueFromText: function(t) { return parseInt(t, 10) || from }
                onModified: function(v) { root.setSetting(modelData.key, v) }
              }
            }
          }

          PanelSectionHeader {
            text: "BROWSER ERRORS"
            foreground: root.barForeground
            fontFamily: root.ff
          }

          Row {
            width: parent.width
            spacing: Style.space(14)

            NumberField {
              id: portField
              label: Model.PORT_SETTING.label
              from: Model.PORT_SETTING.from
              to: Model.PORT_SETTING.to
              value: Number(root.settings.jsPort || 9787)
              foreground: root.barForeground
              fontFamily: root.ff
              fontSize: Style.font.bodySmall
              field.textFromValue: function(v) { return String(v) }
              field.valueFromText: function(t) { return parseInt(t, 10) || from }
              onModified: function(v) { root.setSetting("jsPort", v) }
            }

            Column {
              width: parent.width - portField.width - parent.spacing
              spacing: Style.spacing.md

              Text {
                textFormat: Text.PlainText
                text: "Other origins of your apps"
                color: root.dimForeground
                font.family: root.ff
                font.pixelSize: Style.font.bodySmall
              }

              TextField {
                id: originsField
                width: parent.width
                placeholderText: "http://shop.test:3000"
                foreground: root.barForeground
                font.family: root.ff
                font.pixelSize: Style.font.bodySmall
                onEditingFinished: root.saveOrigins()
                Keys.onEscapePressed: { text = root.originsText; keyCatcher.forceActiveFocus() }
              }
            }
          }

          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
            text: "Pages on localhost, 127.0.0.1 and *.localhost always report. Separate other origins with spaces."
            color: root.dimForeground
            font.family: root.ff
            font.pixelSize: Style.font.caption
          }

          PanelSectionHeader {
            text: "IGNORED"
            foreground: root.barForeground
            fontFamily: root.ff
          }

          Item {
            width: parent.width
            implicitHeight: Math.max(ignoredLabel.implicitHeight, unignoreButton.implicitHeight)

            Text {
              id: ignoredLabel
              anchors.left: parent.left
              anchors.right: unignoreButton.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
              text: Model.ignoredText(root.ignoredRows.length)
              color: root.barForeground
              font.family: root.ff
              font.pixelSize: Style.font.bodySmall
            }

            Button {
              id: unignoreButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              visible: root.ignoredRows.length > 1
              iconText: "\u{f0208}" // nf-md-eye
              iconSize: Style.font.icon
              text: "Show all again"
              tooltipText: "Forget every ignored problem; they show again when they happen"
              fontSize: Style.font.bodySmall
              foreground: root.barForeground
              fontFamily: root.ff
              bordered: true
              horizontalPadding: Style.spacing.controlPaddingX
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: if (root.service) root.service.unignore()
            }
          }

          Flickable {
            id: ignoredView
            visible: root.ignoredRows.length > 0
            width: parent.width
            height: Math.min(ignoredList.implicitHeight, Style.space(220))
            contentHeight: ignoredList.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
              id: ignoredList
              width: parent.width
              spacing: Style.space(2)

              Repeater {
                model: root.ignoredRows
                Rectangle {
                  id: ignoredRow
                  required property var modelData
                  width: ignoredList.width
                  implicitHeight: ignoredText.implicitHeight + Style.spacing.controlPaddingY * 2
                  radius: Style.space(4)
                  color: ignoredMouse.containsMouse ? Style.hoverFillFor(root.barForeground, root.accent) : "transparent"

                  MouseArea {
                    id: ignoredMouse
                    anchors.fill: parent
                    hoverEnabled: true
                  }

                  Column {
                    id: ignoredText
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.controlPaddingX
                    anchors.right: showAgain.left
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)

                    Text {
                      width: parent.width
                      textFormat: Text.PlainText
                      elide: Text.ElideRight
                      text: ignoredRow.modelData.title
                      color: root.barForeground
                      font.family: root.ff
                      font.pixelSize: Style.font.bodySmall
                    }

                    Text {
                      visible: text !== ""
                      width: parent.width
                      textFormat: Text.PlainText
                      elide: Text.ElideMiddle
                      text: ignoredRow.modelData.sub
                      color: root.dimForeground
                      font.family: root.ff
                      font.pixelSize: Style.font.caption
                    }
                  }

                  PanelActionButton {
                    id: showAgain
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.controlPaddingX
                    anchors.verticalCenter: parent.verticalCenter
                    iconText: "\u{f0208}" // nf-md-eye
                    tooltipText: "Show it again when it happens"
                    foreground: root.barForeground
                    fontFamily: root.ff
                    onClicked: if (root.service) root.service.unignore(ignoredRow.modelData.project, ignoredRow.modelData.fp)
                  }
                }
              }
            }
          }
        }

        Text {
          visible: root.message !== null
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: root.message ? root.message.text + "  ·  " + Model.timeAgo(root.message.ts, root.now) : ""
          color: root.message && !root.message.ok ? root.urgent : root.dimForeground
          font.family: root.ff
          font.pixelSize: Style.font.caption
        }

        PanelSeparator {
          width: parent.width
          foreground: root.barForeground
        }

        // ---------- Actions ----------
        Row {
          id: actionRow
          width: parent.width
          spacing: Style.space(6)

          readonly property var entries: [
            { key: "add", label: "Add project", icon: "\u{f0257}", tip: "Choose a Rails application to watch its development.log" }, // nf-md-folder_plus
            { key: "remove", label: "Remove project", icon: "\u{f0258}", tip: "Stop watching the chosen project; the project itself is not touched" }, // nf-md-folder_remove
            { key: "scan", label: "Scan log", icon: "\u{f0349}", tip: "Read the last 5 MB of the log again for problems" }, // nf-md-magnify
            { key: "settings", label: "Settings", icon: "\u{f0493}", tip: "Notifications, thresholds, the collector port and ignored problems" } // nf-md-cog
          ]

          Repeater {
            model: actionRow.entries
            Button {
              required property var modelData
              width: (actionRow.width - actionRow.spacing * (actionRow.entries.length - 1)) / actionRow.entries.length
              enabled: modelData.key === "settings" || (!root.configuring && (modelData.key === "add" || root.project !== null))
              opacity: enabled ? 1 : 0.4
              iconText: modelData.key === "settings" && root.configuring ? "\u{f004d}" : modelData.icon // nf-md-arrow_left
              iconSize: Style.font.icon
              text: modelData.key === "settings" && root.configuring ? "Back" : modelData.label
              tooltipText: modelData.tip
              fontSize: Style.font.bodySmall
              foreground: root.barForeground
              fontFamily: root.ff
              bordered: true
              horizontalPadding: Style.spacing.controlPaddingX
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: {
                if (modelData.key === "add") root.addProject()
                else if (modelData.key === "remove") root.removeProject()
                else if (modelData.key === "scan") root.scan()
                else root.configure()
              }
            }
          }
        }
      }
    }
  }
}
