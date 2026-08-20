'use client'

import ChatInput from './ChatInput'
import MessageArea from './MessageArea'
const ChatArea = () => {
  return (
    <main className="relative m-1.5 flex min-h-0 min-w-0 flex-grow flex-col overflow-hidden rounded-xl bg-background">
      <MessageArea />
      <div className="ml-9 shrink-0 px-4 pb-2 pt-1">
        <ChatInput />
      </div>
    </main>
  )
}

export default ChatArea
