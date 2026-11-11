import type { MeetingConfig } from '../config'
import { ClientTokenProvider } from './clientTokenProvider'
import { HttpTokenProvider } from './httpTokenProvider'
import type { CredentialProvider } from './types'

export function createCredentialProvider(
  config: MeetingConfig,
): CredentialProvider {
  if (config.tokenMode === 'server') {
    return new HttpTokenProvider(config.tokenEndpoint)
  }
  return new ClientTokenProvider(config)
}
