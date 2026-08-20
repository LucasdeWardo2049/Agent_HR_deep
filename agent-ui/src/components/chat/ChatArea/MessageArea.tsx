'use client'

import { useStore } from '@/store'
import Messages from './Messages'
import ScrollToBottom from '@/components/chat/ChatArea/ScrollToBottom'
import { useEffect } from 'react'
import { StickToBottom, useStickToBottomContext } from 'use-stick-to-bottom'

const FollowNewMessages = ({ messageCount }: { messageCount: number }) => {
  const { scrollToBottom } = useStickToBottomContext()

  useEffect(() => {
    void scrollToBottom({ animation: 'instant', ignoreEscapes: true })
  }, [messageCount, scrollToBottom])

  return null
}

const MessageArea = () => {
  const messages = useStore((state) => state.messages)

  return (
    <StickToBottom
      className="relative flex min-h-0 flex-1 flex-col overflow-hidden"
      resize="instant"
      initial="instant"
    >
      <FollowNewMessages messageCount={messages.length} />
      <StickToBottom.Content
        className={`flex min-h-full flex-col ${
          messages.length === 0 ? 'justify-center' : ''
        }`}
        scrollClassName="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div className="mx-auto w-full max-w-2xl space-y-9 px-4 py-6">
          <Messages messages={messages} />
        </div>
      </StickToBottom.Content>
      <ScrollToBottom />
    </StickToBottom>
  )
}

export default MessageArea
