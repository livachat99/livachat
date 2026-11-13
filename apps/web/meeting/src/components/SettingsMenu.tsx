import { useMaybeLayoutContext } from '@livekit/components-react'
import * as React from 'react'
import type { MeetingConfig } from '../config'
import { messages } from '../messages'
import { CameraSettings } from './CameraSettings'
import { MicrophoneSettings } from './MicrophoneSettings'
import { SpeakerSettings } from './SpeakerSettings'
import styles from './SettingsMenu.module.css'

export type SettingsMenuProps = React.HTMLAttributes<HTMLDivElement> & {
  config: MeetingConfig
}

export function SettingsMenu({ config, ...props }: SettingsMenuProps) {
  const layoutContext = useMaybeLayoutContext()
  const tabs = ['media'] as const
  const [activeTab, setActiveTab] = React.useState<(typeof tabs)[number]>(
    tabs[0],
  )

  return (
    <div
      className="settings-menu"
      style={{ width: '100%', position: 'relative' }}
      {...props}
    >
      <div className={styles.header}>
        <div className={styles.tabs}>
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              className={`${styles.tab} lk-button`}
              onClick={() => setActiveTab(tab)}
              aria-pressed={tab === activeTab}
            >
              {messages.settingsMedia}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="lk-button"
          onClick={() =>
            layoutContext?.widget.dispatch?.({ msg: 'toggle_settings' })
          }
        >
          {messages.settingsClose}
        </button>
      </div>

      {activeTab === 'media' ? (
        <div className={styles.content}>
          <section>
            <h3>{messages.settingsCamera}</h3>
            <CameraSettings
              enableBackgroundEffects={config.enableBackgroundEffects}
              backgroundEffectsPreload={config.backgroundEffectsPreload}
              backgroundImages={config.backgroundImages}
              mediapipeAssetBase={config.mediapipeAssetBase}
            />
          </section>

          <section>
            <h3>{messages.settingsMicrophone}</h3>
            <MicrophoneSettings />
          </section>

          <section>
            <h3>{messages.settingsSpeaker}</h3>
            <SpeakerSettings />
          </section>
        </div>
      ) : null}
    </div>
  )
}
