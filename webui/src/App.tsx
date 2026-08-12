import { useState } from 'react'
import { Toaster } from 'sonner'
import { Sidebar } from '@/components/layout/Sidebar'
import { MainContent } from '@/components/layout/MainContent'
import { AuthorFooter } from '@/components/layout/AuthorFooter'
import { CrawlerConfigPanel } from '@/components/config/CrawlerConfigPanel'
import { EnvironmentCheck, isEnvChecked } from '@/components/env/EnvironmentCheck'
import { LicenseDisclaimer, isLicenseAccepted } from '@/components/license/LicenseDisclaimer'
import { TaskCenter,ContentLibrary,MediaLibrary,Schedules,HealthPage } from '@/components/product/ProductPages'

function App() {
  // Initialize by checking localStorage if license has been accepted
  const [licenseAccepted, setLicenseAccepted] = useState(() => isLicenseAccepted())
  // Initialize by checking localStorage if env check has passed
  const [envChecked, setEnvChecked] = useState(() => isEnvChecked())
  // State for showing disclaimer manually
  const [showDisclaimer, setShowDisclaimer] = useState(false)
  const [page,setPage]=useState<'crawl'|'tasks'|'content'|'media'|'schedules'|'health'>('crawl')

  const handleEnvCheckComplete = () => {
    setEnvChecked(true)
  }

  const handleLicenseAccept = () => {
    setLicenseAccepted(true)
    setShowDisclaimer(false)
  }

  const handleShowDisclaimer = () => {
    setShowDisclaimer(true)
  }

  return (
    <div className="flex flex-col h-screen cyber-grid overflow-hidden relative">
      {/* License Disclaimer Modal - Shows first or when triggered */}
      {(!licenseAccepted || showDisclaimer) && (
        <LicenseDisclaimer onAccept={handleLicenseAccept} />
      )}

      {/* Environment Check Modal - Shows after license accepted */}
      {licenseAccepted && !showDisclaimer && !envChecked && (
        <EnvironmentCheck onCheckComplete={handleEnvCheckComplete} />
      )}

      {/* Header Bar */}
      <Sidebar onShowDisclaimer={handleShowDisclaimer} />

      <nav className="px-4 pt-3 flex gap-2 flex-wrap">{([['crawl','采集控制'],['tasks','任务中心'],['content','内容库'],['media','媒体库'],['schedules','定时计划'],['health','系统健康']] as const).map(([id,label])=><button key={id} onClick={()=>setPage(id)} className={`px-3 py-1.5 rounded-lg text-xs font-mono border ${page===id?'border-cyber-neon-cyan text-cyber-neon-cyan':'border-cyber-border-subtle text-cyber-text-muted'}`}>{label}</button>)}</nav>

      {/* Main Area */}
      <div className="flex-1 flex flex-col gap-4 p-4 overflow-hidden min-h-0">
        {/* Config Panel - Primary Action Area (Always Expanded) */}
        {page==='crawl'?<><div className="flex-shrink-0">
          <CrawlerConfigPanel />
        </div>

        {/* Console - Collapsible Terminal */}
        <MainContent />
        </>:null}
        {page==='tasks'?<TaskCenter/>:null}{page==='content'?<ContentLibrary/>:null}{page==='media'?<MediaLibrary/>:null}{page==='schedules'?<Schedules/>:null}{page==='health'?<HealthPage/>:null}
      </div>

      {/* Author Footer */}
      <AuthorFooter />

      {/* Toast notifications - Theme-aware style */}
      <Toaster
        position="top-right"
        toastOptions={{
          className: 'glass-panel font-mono text-cyber-text-primary',
          style: {
            fontFamily: 'JetBrains Mono, monospace',
          },
        }}
      />
    </div>
  )
}

export default App
