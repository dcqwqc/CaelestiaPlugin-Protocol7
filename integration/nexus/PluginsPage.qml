pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import Caelestia.Components
import Caelestia.Config
import Caelestia.Plugins
import qs.components
import qs.components.controls
import qs.services
import qs.modules.nexus.common

PageBase {
    id: root

    // Source of truth: the native loader, not example catalog records.
    // Explicit catalog/network installation is intentionally out of scope.
    readonly property var installedPlugins: Plugins.plugins
    readonly property var problemPlugins: Plugins.plugins.filter(p => !p.valid)

    title: qsTr("Plugins")
    maxWidth: Tokens.sizes.nexus.maxContentWidth * 2
    horizontalPadding: Tokens.padding.extraLarge

    ColumnLayout {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        width: root.cappedWidth
        spacing: Tokens.spacing.extraSmall / 2

        StyledText {
            Layout.topMargin: Tokens.spacing.large
            Layout.alignment: Qt.AlignHCenter
            visible: root.installedPlugins.length === 0
            text: qsTr("No plugins installed")
            color: Colours.palette.m3outline
            font: Tokens.font.body.medium
        }

        SectionHeader {
            first: true
            text: qsTr("Installed plugins")
            color: Colours.palette.m3onSurface
            font: Tokens.font.title.medium
        }

        PluginGrid {
            plugins: root.installedPlugins
        }

        SectionHeader {
            visible: root.problemPlugins.length > 0
            text: qsTr("Needs attention")
            color: Colours.palette.m3onSurface
            font: Tokens.font.title.medium
        }

        Repeater {
            model: root.problemPlugins
            InfoRow {
                required property var modelData
                icon: "error"
                iconColour: Colours.palette.m3error
                label: modelData.name || modelData.dir
                subtext: modelData.error
            }
        }

        SectionHeader {
            text: qsTr("Plugin catalog")
            color: Colours.palette.m3onSurface
            font: Tokens.font.title.medium
        }

        StyledText {
            Layout.fillWidth: true
            text: qsTr("The official plugin catalog and updates are still in development. Installed plugins are listed above.")
            color: Colours.palette.m3outline
            font: Tokens.font.body.medium
            wrapMode: Text.WordWrap
        }
    }

    component PluginGrid: LazyGridView {
        id: grid

        property alias plugins: model.values

        Layout.fillWidth: true
        implicitHeight: animatedContentHeight

        cellWidth: 300 // Minimum cell width, elements stretch to fill complete view width
        rowSpacing: Tokens.spacing.large
        columnSpacing: Tokens.spacing.large
        estimatedRowHeight: 426 // Height per plugin card with default tokens and 2 line description, at nexus initial size

        asynchronous: true
        cacheBuffer: 400
        readyDelay: 1

        // Expressive default spatial spring params
        stiffness: 380
        damping: 0.8

        enterDuration: Tokens.anim.durations.expressiveDefaultEffects
        removeDuration: Tokens.anim.durations.expressiveDefaultEffects
        easing: Tokens.anim.expressiveDefaultEffects

        useCustomViewport: true
        viewport: {
            tWatcher.transform; // mapToItem is not reactive so use this to trigger updates
            return Qt.rect(0, root.flickable.contentY - mapToItem(root.flickable.contentItem, 0, 0).y, width, root.flickable.height);
        }

        model: ScriptModel {
            id: model
        }

        delegate: PluginCard {}

        // Frame animation to trigger move/resize springs
        FrameAnimation {
            running: grid.animating
            onTriggered: grid.step(frameTime)
        }

        TransformWatcher {
            id: tWatcher

            a: root.flickable.contentItem
            b: grid
        }
    }

    component PluginCard: StyledRect {
        id: plugin

        required property int index
        required property var modelData

        readonly property bool isInstalled: modelData.id !== undefined

        color: Colours.tPalette.m3surfaceContainer
        radius: Tokens.rounding.extraLarge

        implicitHeight: heroWrapper.implicitHeight + detailLayout.implicitHeight + detailLayout.anchors.topMargin + detailLayout.anchors.margins

        StyledClippingRect {
            id: heroWrapper

            anchors.left: parent.left
            anchors.right: parent.right
            implicitHeight: width * 9 / 16

            color: Colours.layer(Colours.palette.m3surfaceContainerHighest, 2)
            radius: Tokens.rounding.extraLarge

            MaterialIcon {
                anchors.centerIn: parent
                text: "image"
                color: Colours.palette.m3outline
                fontStyle: Tokens.font.icon.extraLarge
                opacity: 1 - heroImage.opacity
            }

            Image {
                id: heroImage

                anchors.fill: parent
                source: plugin.modelData.image ?? ""
                retainWhileLoading: true
                opacity: status === Image.Ready ? 1 : 0

                Behavior on opacity {
                    Anim {
                        type: Anim.SlowEffects
                    }
                }
            }
        }

        ColumnLayout {
            id: detailLayout

            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: heroWrapper.bottom
            anchors.bottom: parent.bottom
            anchors.margins: Tokens.padding.large
            anchors.topMargin: Tokens.spacing.medium

            spacing: Tokens.spacing.extraSmall

            StyledText {
                Layout.fillWidth: true
                text: plugin.modelData.name
                font: Tokens.font.title.builders.medium.width(110).build()
                wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            }

            StyledText {
                Layout.topMargin: -parent.spacing / 2
                Layout.fillWidth: true
                text: qsTr("By %1 · v%2").arg(plugin.modelData.author).arg(plugin.modelData.version)
                color: Colours.palette.m3outline
                font: Tokens.font.body.small
                wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            }

            StyledText {
                Layout.topMargin: Tokens.spacing.extraSmall
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: plugin.modelData.description || ""
                color: Colours.palette.m3onSurfaceVariant
                font: Tokens.font.body.small
                wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            }

            Flow {
                Layout.topMargin: Tokens.spacing.small
                Layout.fillWidth: true
                spacing: Tokens.spacing.extraSmall

                Repeater {
                    model: plugin.modelData.tags ?? []

                    StyledRect {
                        required property string modelData

                        color: Colours.palette.m3tertiaryContainer
                        radius: Tokens.rounding.full

                        implicitWidth: tagText.implicitWidth + Tokens.padding.medium * 2
                        implicitHeight: tagText.implicitHeight + Tokens.padding.extraSmall * 2

                        StyledText {
                            id: tagText

                            anchors.centerIn: parent
                            text: parent.modelData
                            color: Colours.palette.m3onTertiaryContainer
                            font: Tokens.font.label.small
                        }
                    }
                }
            }

            StyledText {
                Layout.fillWidth: true
                visible: !plugin.modelData.valid
                text: plugin.modelData.error ?? ""
                color: Colours.palette.m3error
                font: Tokens.font.body.small
                wrapMode: Text.Wrap
            }

            TextButton {
                Layout.topMargin: Tokens.spacing.medium
                Layout.fillWidth: true
                isRound: true
                shapeMorph: true
                text: plugin.modelData.enabled ? qsTr("Disable") : qsTr("Enable")
                font: Tokens.font.body.builders.small.width(110).build()
                enabled: plugin.modelData.valid
                onClicked: Plugins.setPluginEnabled(plugin.modelData.id, !plugin.modelData.enabled)
            }
        }
    }
}
