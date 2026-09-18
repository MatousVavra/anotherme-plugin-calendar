function calendarPlugin() {
    return {
        view: 'month',
        cursor: null,
        now: null,
        events: [],
        loading: false,
        selectedDate: null,
        search: '',
        dialogOpen: false,
        editing: null,
        saving: false,
        deleteMode: null,
        formTitle: '',
        formDate: '',
        formTime: '',
        formEndDate: '',
        formRecurrence: '',
        formRecurrenceEnd: '',
        formColor: '',
        formNote: '',
        dragEvent: null,
        _nowTimer: null,

        COLORS: {
            tomato: '#d97d86',
            flamingo: '#e8a0a8',
            banana: '#f5cc89',
            sage: '#a8c4a0',
            basil: '#89c4a1',
            peacock: '#7fb3d5',
            blueberry: '#8f9bd9',
            lavender: '#c4b8e0',
            graphite: '#948fa8',
        },

        init() {
            this.cursor = new Date();
            this.selectedDate = this.localDate();
            this.now = new Date();
            this._nowTimer = setInterval(() => { this.now = new Date(); }, 60000);
            AM.onCleanup(() => { clearInterval(this._nowTimer); });
            this.loadEvents();
        },

        localDate(d = new Date()) {
            const offset = d.getTimezoneOffset();
            return new Date(d.getTime() - offset * 60000).toISOString().split('T')[0];
        },

        monthMatrix() {
            const y = this.cursor.getFullYear(), m = this.cursor.getMonth();
            const first = new Date(y, m, 1);
            const offset = (first.getDay() + 6) % 7;
            const start = new Date(y, m, 1 - offset);
            const weeks = [];
            for (let w = 0; w < 6; w++) {
                const week = [];
                for (let d = 0; d < 7; d++) {
                    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + w * 7 + d);
                    week.push({ date: day, dateStr: this.localDate(day), inMonth: day.getMonth() === m, isToday: this.localDate(day) === this.localDate() });
                }
                weeks.push(week);
            }
            return weeks;
        },

        weekDays() {
            const d = new Date(this.cursor);
            const offset = (d.getDay() + 6) % 7;
            d.setDate(d.getDate() - offset);
            const days = [];
            for (let i = 0; i < 7; i++) {
                const day = new Date(d);
                days.push({ date: day, dateStr: this.localDate(day), isToday: this.localDate(day) === this.localDate() });
                d.setDate(d.getDate() + 1);
            }
            return days;
        },

        visibleRange() {
            if (this.view === 'week') {
                const days = this.weekDays();
                return { from: days[0].dateStr, to: days[6].dateStr };
            }
            if (this.view === 'agenda') {
                const now = new Date();
                return {
                    from: this.localDate(new Date(now.getTime() - 90 * 86400000)),
                    to: this.localDate(new Date(now.getTime() + 180 * 86400000)),
                };
            }
            const weeks = this.monthMatrix();
            return { from: weeks[0][0].dateStr, to: weeks[5][6].dateStr };
        },

        periodLabel() {
            const d = this.cursor;
            if (this.view === 'week') {
                const days = this.weekDays();
                const a = days[0].date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                const b = days[6].date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
                return a + ' – ' + b;
            }
            if (this.view === 'agenda') return 'Agenda';
            return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        },

        async loadEvents() {
            this.loading = true;
            try {
                const { from, to } = this.visibleRange();
                const resp = await AM.fetch('/plugins/calendar/events?from=' + from + '&to=' + to);
                if (!resp || !resp.ok) { AM.toast('Failed to load events', 'error'); return; }
                const data = await resp.json();
                this.events = data.events || [];
            } catch (e) {
                console.error('Failed to load calendar events', e);
                AM.toast('Failed to load events', 'error');
            } finally { this.loading = false; }
        },

        switchView(v) {
            this.view = v;
            this.loadEvents();
        },

        prev() {
            this.cursor = this.shiftPeriod(-1);
            this.loadEvents();
        },

        next() {
            this.cursor = this.shiftPeriod(1);
            this.loadEvents();
        },

        goToday() {
            this.cursor = new Date();
            this.loadEvents();
        },

        shiftPeriod(dir) {
            const d = new Date(this.cursor);
            if (this.view === 'week') d.setDate(d.getDate() + 7 * dir);
            else if (this.view === 'agenda') d.setDate(d.getDate() + 30 * dir);
            else d.setMonth(d.getMonth() + dir);
            return d;
        },

        onSearch() {
            if (this.search && this.view !== 'agenda') this.switchView('agenda');
        },

        eventsForDay(dateStr) {
            const list = this.events.filter(e => e.date === dateStr);
            if (this.search) {
                const q = this.search.toLowerCase();
                return list.filter(e => (e.title || '').toLowerCase().includes(q));
            }
            return list;
        },

        agendaGroups() {
            const groups = [];
            let byDate = {};
            const q = this.search ? this.search.toLowerCase() : '';
            for (const e of this.events) {
                if (q && !(e.title || '').toLowerCase().includes(q)) continue;
                if (!byDate[e.date]) { byDate[e.date] = []; groups.push(e.date); }
                byDate[e.date].push(e);
            }
            groups.sort();
            return groups.map(d => ({ date: d, events: byDate[d] }));
        },

        isMultiDay(ev) {
            return !!(ev.end_date && ev.end_date > ev.series_date && !ev.recurrence);
        },

        allDayEvents(dateStr) {
            return this.eventsForDay(dateStr).filter(e => !e.time || this.isMultiDay(e));
        },

        timedEvents(dateStr) {
            return this.eventsForDay(dateStr).filter(e => e.time && !this.isMultiDay(e));
        },

        eventTop(ev) {
            const [h, m] = (ev.time || '00:00').split(':').map(Number);
            return ((h * 60 + m) / 1440) * this.weekGridHeight();
        },

        eventHeight(ev) {
            return Math.max(24, (30 / 1440) * this.weekGridHeight());
        },

        weekGridHeight() {
            return 24 * 48;
        },

        nowLineTop(dateStr) {
            if (dateStr !== this.localDate(this.now)) return null;
            const h = this.now.getHours(), m = this.now.getMinutes();
            return ((h * 60 + m) / 1440) * this.weekGridHeight();
        },

        colorHex(key) {
            return this.COLORS[key] || this.COLORS.lavender;
        },

        chipStyle(ev) {
            return { background: this.colorHex(ev.color) + '22', color: this.colorHex(ev.color) };
        },

        openCreate(dateStr, time) {
            this.editing = null;
            this.deleteMode = null;
            this.formTitle = '';
            this.formDate = dateStr || this.selectedDate || this.localDate();
            this.formTime = time || '';
            this.formEndDate = '';
            this.formRecurrence = '';
            this.formRecurrenceEnd = '';
            this.formColor = '';
            this.formNote = '';
            this.dialogOpen = true;
        },

        openEdit(ev) {
            this.editing = ev;
            this.deleteMode = null;
            this.formTitle = ev.title || '';
            this.formDate = ev.series_date || ev.date;
            this.formTime = ev.time || '';
            this.formEndDate = (!ev.recurrence && ev.end_date && ev.end_date > (ev.series_date || ev.date)) ? ev.end_date : '';
            this.formRecurrence = ev.recurrence || '';
            this.formRecurrenceEnd = (ev.recurrence && ev.end_date) ? ev.end_date : '';
            this.formColor = ev.color || '';
            this.formNote = ev.note || '';
            this.dialogOpen = true;
        },

        closeDialog() {
            this.dialogOpen = false;
            this.editing = null;
            this.deleteMode = null;
        },

        async saveEvent() {
            const title = this.formTitle.trim();
            if (!title || !this.formDate) return;
            this.saving = true;
            try {
                const body = { title, date: this.formDate };
                if (this.formTime) body.time = this.formTime;
                if (this.formNote.trim()) body.note = this.formNote.trim();
                if (this.formRecurrence) {
                    body.recurrence = this.formRecurrence;
                    if (this.formRecurrenceEnd) body.end_date = this.formRecurrenceEnd;
                } else if (this.formEndDate) {
                    body.end_date = this.formEndDate;
                }
                if (this.formColor) body.color = this.formColor;
                const resp = this.editing
                    ? await AM.fetch('/plugins/calendar/events/' + this.editing.id, { method: 'PUT', body })
                    : await AM.fetch('/plugins/calendar/events', { method: 'POST', body });
                if (!resp || !resp.ok) {
                    AM.toast('Failed to save event (HTTP ' + (resp ? resp.status : '?') + ')', 'error');
                    return;
                }
                AM.toast(this.editing ? 'Event updated' : 'Event saved', 'success');
                this.closeDialog();
                await this.loadEvents();
            } catch (e) {
                AM.toast('Failed to save event', 'error');
            } finally { this.saving = false; }
        },

        async deleteEvent(single) {
            if (!this.editing) return;
            const ev = this.editing;
            if (single) {
                const ok = confirm('Delete only the occurrence on ' + this.fmtDayLabel(ev.date) + '? The series stays.');
                if (!ok) return;
            } else if (!confirm('Delete ' + (ev.recurrence ? 'the whole series' : 'this event') + '?')) {
                return;
            }
            this.saving = true;
            try {
                let url = '/plugins/calendar/events/' + ev.id;
                if (single) url += '?date=' + encodeURIComponent(ev.date);
                const resp = await AM.fetch(url, { method: 'DELETE' });
                if (!resp || !resp.ok) {
                    if (resp && resp.status === 400) AM.toast('Only repeating events can delete a single occurrence', 'error');
                    else AM.toast('Failed to delete event', 'error');
                    return;
                }
                AM.toast('Event deleted', 'success');
                this.closeDialog();
                await this.loadEvents();
            } catch (e) {
                AM.toast('Failed to delete event', 'error');
            } finally { this.saving = false; }
        },

        async moveEvent(ev, dateStr, time) {
            const body = { date: dateStr };
            if (time !== undefined) body.time = time;
            if (ev.recurrence && !confirm('This is a repeating event — move all occurrences?')) return;
            const resp = await AM.fetch('/plugins/calendar/events/' + ev.id, { method: 'PUT', body });
            if (!resp || !resp.ok) {
                AM.toast('Failed to move event', 'error');
                await this.loadEvents();
                return;
            }
            AM.toast('Event moved', 'success');
            await this.loadEvents();
        },

        onChipDragStart(ev, e) {
            this.dragEvent = ev;
            if (e.dataTransfer) {
                e.dataTransfer.effectAllowed = 'move';
                try { e.dataTransfer.setData('text/plain', ev.id); } catch (err) {}
            }
        },

        onDayDrop(day, e) {
            const ev = this.dragEvent;
            this.dragEvent = null;
            if (!ev || ev.date === day.dateStr) return;
            e.preventDefault();
            this.moveEvent(ev, day.dateStr, undefined);
        },

        onWeekDrop(day, e) {
            const ev = this.dragEvent;
            this.dragEvent = null;
            if (!ev) return;
            e.preventDefault();
            const minutes = this.minutesFromEventY(e);
            const time = this.minutesToHHMM(minutes);
            this.moveEvent(ev, day.dateStr, time);
        },

        minutesFromEventY(e) {
            const rect = e.currentTarget.getBoundingClientRect();
            const y = Math.max(0, e.clientY - rect.top);
            return Math.min(1439, Math.round((y / this.weekGridHeight()) * 1440 / 15) * 15);
        },

        minutesToHHMM(minutes) {
            return String(Math.floor(minutes / 60)).padStart(2, '0') + ':' + String(minutes % 60).padStart(2, '0');
        },

        weekSlotClick(day, e) {
            const time = this.minutesToHHMM(this.minutesFromEventY(e));
            this.openCreate(day.dateStr, time);
        },

        fmtTime(t) {
            if (!t) return '';
            const [h, m] = t.split(':').map(Number);
            const ampm = h >= 12 ? 'PM' : 'AM';
            const hr = h % 12 === 0 ? 12 : h % 12;
            return m === 0 ? hr + ' ' + ampm : hr + ':' + String(m).padStart(2, '0') + ' ' + ampm;
        },

        fmtDayLabel(dateStr) {
            const d = new Date(dateStr + 'T00:00:00');
            return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
        },
    };
}
