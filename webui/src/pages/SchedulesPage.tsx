import { Schedules } from '@/components/product/ProductPages'
import { PageHeader } from '@/components/product/Primitives'
export default function SchedulesPage(){return <div><PageHeader eyebrow="自动化" title="定时计划" description="为抖音账号或话题设置单次、每小时或每日增量采集。应用关闭期间错过的计划只补执行一次。"/><Schedules/></div>}
