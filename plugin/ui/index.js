function calendarPlugin() {
    return {
        events: [],
        formTitle: '',
        formDate: '',
        formTime: '',
        formNote: '',
        saving: false,
        showPast: false,

        async init() {
            this.formDate = new Date().toISOString().split('T')[0];
            await this.loadCalendarEvents();
            AM.onCleanup(() => {});
        },

        async loadCalendarEvents() {
            try {
                const now = new Date();
                let from, to;
                if (this.showPast) {
                    from = new Date(now.getTime() - 90 * 86400000).toISOString().split('T')[0];
                    to = now.toISOString().split('T')[0];
                } else {
                    from = now.toISOString().split('T')[0];
                    to = new Date(now.getTime() + 90 * 86400000).toISOString().split('T')[0];
                }
                const resp = await AM.fetch('/plugins/calendar/events?from=' + from + '&to=' + to);
                if (resp) this.events = await resp.json();
            } catch (e) { console.error('Failed to load calendar events', e); }
        },

        togglePast() {
            this.showPast = !this.showPast;
            this.loadCalendarEvents();
        },

        async saveCalendarEvent() {
            const title = this.formTitle.trim();
            if (!title || !this.formDate) return;
            this.saving = true;
            try {
                const body = { title, date: this.formDate };
                if (this.formTime) body.time = this.formTime;
                if (this.formNote.trim()) body.note = this.formNote.trim();
                await AM.fetch('/plugins/calendar/events', { method: 'POST', body });
                AM.toast('Event saved', 'success');
                this.formTitle = '';
                this.formDate = '';
                this.formTime = '';
                this.formNote = '';
                await this.loadCalendarEvents();
            } catch (e) { AM.toast(e.message, 'error'); }
            finally { this.saving = false; }
        },

        async deleteCalendarEvent(id) {
            if (!confirm('Delete this event?')) return;
            try {
                await AM.fetch('/plugins/calendar/events/' + id, { method: 'DELETE' });
                this.events = this.events.filter(e => e.id !== id);
                AM.toast('Event deleted', 'success');
            } catch (e) { AM.toast(e.message, 'error'); }
        },
    };
}
